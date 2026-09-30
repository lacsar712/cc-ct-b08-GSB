from django.conf import settings
from django.db import transaction
from django.utils import timezone

from desk.models import (
    ArchiveEntry,
    ArchiveError,
    ArchivePackage,
    OffsetSubmission,
)


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def build_reason(tool_code: str, offset_um: int, verdict: str) -> str:
    """签发当刻写进包内、随后一起冻结的判定理由。"""
    tol = settings.OFFSET_TOLERANCE_UM
    if verdict == OffsetSubmission.Verdict.FAIL:
        return (
            f"{tool_code} 刀补 {offset_um}µm，绝对值 {abs(offset_um)}µm "
            f"超出容差 {tol}µm，判定超差。"
        )
    return (
        f"{tool_code} 刀补 {offset_um}µm，绝对值 {abs(offset_um)}µm "
        f"不超过容差 {tol}µm，判定合格。"
    )


def _require_open(package: ArchivePackage) -> None:
    if package.is_sealed:
        raise ArchiveError("归档包已封只读，不能再变更")


@transaction.atomic
def create_package(name: str, created_by) -> ArchivePackage:
    name = (name or "").strip()
    if not name:
        raise ArchiveError("归档包名称不能为空")
    return ArchivePackage.objects.create(name=name, created_by=created_by)


@transaction.atomic
def queue_entry(package: ArchivePackage, submission: OffsetSubmission) -> ArchiveEntry:
    _require_open(package)
    if package.entries.filter(submission_id=submission.pk).exists():
        raise ArchiveError("该刀补记录已在本包栏目中")
    return ArchiveEntry.objects.create(package=package, submission=submission)


@transaction.atomic
def sign_entry(entry_id: int) -> ArchiveEntry:
    """签发：把刀号 / 刀补 / 判定 / 理由冻进包，定格在签发当刻。

    用行锁把包、条目、来源刀补一并锁定，保证读到的是签发当刻的现行值；
    一旦由 queued 翻成 signed，模型层写口保护立即生效，冻结值不可再改。
    """
    entry = (
        ArchiveEntry.objects.select_for_update()
        .select_related("package", "submission")
        .get(pk=entry_id)
    )
    package = (
        ArchivePackage.objects.select_for_update().get(pk=entry.package_id)
    )
    _require_open(package)
    if entry.state != ArchiveEntry.State.QUEUED:
        raise ArchiveError("该栏目已签发，禁止重复签发或改写")

    submission = (
        OffsetSubmission.objects.select_for_update().get(pk=entry.submission_id)
    )

    # 签发当刻的现行三字段。若判定尚未给出，则按当刻刀补现算一并定格。
    tool_code = submission.tool_code
    offset_um = submission.offset_um
    verdict = submission.verdict or evaluate_verdict(offset_um)
    reason = build_reason(tool_code, offset_um, verdict)

    entry.state = ArchiveEntry.State.SIGNED
    entry.snap_tool_code = tool_code
    entry.snap_offset_um = offset_um
    entry.snap_verdict = verdict
    entry.snap_reason = reason
    entry.signed_at = timezone.now()
    entry.save()
    return entry


@transaction.atomic
def remove_queued_entry(entry_id: int) -> None:
    entry = (
        ArchiveEntry.objects.select_for_update()
        .select_related("package")
        .get(pk=entry_id)
    )
    _require_open(entry.package)
    if entry.state != ArchiveEntry.State.QUEUED:
        raise ArchiveError("已签栏目为只读归档，不能移除")
    entry.delete()


@transaction.atomic
def seal_package(package_id: int) -> ArchivePackage:
    package = ArchivePackage.objects.select_for_update().get(pk=package_id)
    _require_open(package)
    queued = package.entries.filter(state=ArchiveEntry.State.QUEUED).count()
    if queued:
        raise ArchiveError(
            f"还有 {queued} 条排队待签栏目，请先签发或移除后再封包"
        )
    package.status = ArchivePackage.Status.SEALED
    package.sealed_at = timezone.now()
    package.save(update_fields=["status", "sealed_at"])
    return package
