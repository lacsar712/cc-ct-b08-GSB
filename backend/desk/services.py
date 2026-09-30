from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from desk.models import ArchiveEntry, ArchivePackage, OffsetSubmission


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


class ArchiveSignError(Exception):
    """签发被业务规则拒绝（未结清 / 已签发过）。"""


def default_reason(submission: OffsetSubmission) -> str:
    """签发时未填理由则按当刻判定依据生成，随包一起冻结。"""
    tolerance = settings.OFFSET_TOLERANCE_UM
    if submission.verdict == OffsetSubmission.Verdict.PASS:
        return (
            f"刀补 {submission.offset_um}µm，绝对值未超 {tolerance}µm 容差，判定合格"
        )
    return f"刀补 {submission.offset_um}µm，绝对值超出 {tolerance}µm 容差，判定超差"


def sign_submission(
    *,
    submission_id: int,
    reason: str,
    title: str,
    user,
) -> ArchiveEntry:
    """签发：把刀号/刀补/判定/理由冻进新建只读归档包。

    行锁 + 唯一约束双保险，保证同一笔刀补只会被签发一次。
    """
    with transaction.atomic():
        try:
            submission = OffsetSubmission.objects.select_for_update().get(
                pk=submission_id
            )
        except OffsetSubmission.DoesNotExist:
            raise ArchiveSignError("刀补记录不存在") from None

        if submission.status != OffsetSubmission.Status.DONE:
            raise ArchiveSignError("刀补尚未结清，不能签发归档")
        if ArchiveEntry.objects.filter(submission_id=submission.id).exists():
            raise ArchiveSignError("该刀补已签发过，归档包内字段不可改写")

        frozen_reason = reason.strip() or default_reason(submission)
        package_title = title.strip() or (
            f"归档包 {timezone.now():%Y%m%d-%H%M%S} · {user.username}"
        )
        try:
            with transaction.atomic():
                package = ArchivePackage.objects.create(
                    title=package_title,
                    created_by_name=user.username,
                )
                entry = ArchiveEntry.objects.create(
                    package=package,
                    submission=submission,
                    tool_code=submission.tool_code,
                    offset_um=submission.offset_um,
                    verdict=submission.verdict,
                    reason=frozen_reason,
                    signed_by_name=user.username,
                )
        except IntegrityError:
            # 并发签发撞唯一约束：以先签者为准
            raise ArchiveSignError("该刀补已签发过，归档包内字段不可改写") from None
    return entry
