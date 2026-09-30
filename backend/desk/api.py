from datetime import datetime
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import (
    ArchiveEntry,
    ArchiveError,
    ArchivePackage,
    OffsetSubmission,
    User,
)
from desk import services

api = NinjaAPI(title="数控刀补复核台", version="1.0")


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class HealthOut(Schema):
    status: str


class LoginIn(Schema):
    username: str
    password: str


class LoginOut(Schema):
    token: str
    username: str
    role: str
    can_write: bool


class SubmissionIn(Schema):
    tool_code: str
    offset_um: int


class SubmissionOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    status: str
    verdict: str
    created_at: datetime
    reviewed_at: Optional[datetime]


class PackageCreateIn(Schema):
    name: str


class EntryQueueIn(Schema):
    submission_id: int


class ArchiveEntryOut(Schema):
    id: int
    submission_id: int
    state: str
    # 包内冻结三字段 + 理由（签发当刻定格）
    snap_tool_code: str
    snap_offset_um: Optional[int]
    snap_verdict: str
    snap_reason: str
    signed_at: Optional[datetime]
    # 总览现行值（用于打开包对拍）
    live_tool_code: str
    live_offset_um: int
    live_verdict: str
    # 已签后包内快照与现行值是否出现差异
    drift: bool


class PackageOut(Schema):
    id: int
    name: str
    status: str
    created_at: datetime
    sealed_at: Optional[datetime]
    queued_count: int
    signed_count: int


class PackageDetailOut(PackageOut):
    entries: list[ArchiveEntryOut]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_out(row: OffsetSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_code,
        offset_um=row.offset_um,
        status=row.status,
        verdict=row.verdict or "",
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
    )


def require_writer(request: HttpRequest) -> User:
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "观察账号只读，可翻包但不能签发或变更归档包")
    return user


def _guard_archive(fn, *args, **kwargs):
    """把归档只读规则违例翻译成 409，对象缺失翻译成 404。"""
    try:
        return fn(*args, **kwargs)
    except ArchiveError as exc:
        raise HttpError(409, "；".join(exc.messages))
    except ObjectDoesNotExist:
        raise HttpError(404, "归档包或栏目不存在")


def _entry_dict(entry: ArchiveEntry) -> dict:
    live_tool = entry.submission.tool_code
    live_offset = entry.submission.offset_um
    live_verdict = entry.submission.verdict or ""
    drift = (
        entry.state == ArchiveEntry.State.SIGNED
        and (
            entry.snap_tool_code != live_tool
            or entry.snap_offset_um != live_offset
            or entry.snap_verdict != live_verdict
        )
    )
    return {
        "id": entry.id,
        "submission_id": entry.submission_id,
        "state": entry.state,
        "snap_tool_code": entry.snap_tool_code,
        "snap_offset_um": entry.snap_offset_um,
        "snap_verdict": entry.snap_verdict,
        "snap_reason": entry.snap_reason,
        "signed_at": entry.signed_at,
        "live_tool_code": live_tool,
        "live_offset_um": live_offset,
        "live_verdict": live_verdict,
        "drift": drift,
    }


def _package_qs():
    return ArchivePackage.objects.annotate(
        queued_count=Count("entries", filter=Q(entries__state=ArchiveEntry.State.QUEUED)),
        signed_count=Count("entries", filter=Q(entries__state=ArchiveEntry.State.SIGNED)),
    )


def _package_dict(package: ArchivePackage, entries=None) -> dict:
    data = {
        "id": package.id,
        "name": package.name,
        "status": package.status,
        "created_at": package.created_at,
        "sealed_at": package.sealed_at,
        "queued_count": getattr(package, "queued_count", 0) or 0,
        "signed_count": getattr(package, "signed_count", 0) or 0,
    }
    if entries is not None:
        data["entries"] = [_entry_dict(e) for e in entries]
    return data


# --------------------------------------------------------------------------- #
# Health / auth
# --------------------------------------------------------------------------- #
@api.get("/health", response=HealthOut)
def health(request: HttpRequest):
    return {"status": "ok"}


@api.post("/auth/login", response=LoginOut)
def login(request: HttpRequest, body: LoginIn):
    try:
        user = User.objects.get(username=body.username)
    except User.DoesNotExist:
        raise HttpError(401, "用户名或密码错误")
    if not verify_password(body.password, user.password):
        raise HttpError(401, "用户名或密码错误")
    token = create_access_token(user)
    return {
        "token": token,
        "username": user.username,
        "role": user.role,
        "can_write": user.can_write,
    }


# --------------------------------------------------------------------------- #
# 刀补总览（现行值，可随新判定变化）
# --------------------------------------------------------------------------- #
@api.get("/submissions", response=list[SubmissionOut], auth=bearer_auth)
def list_submissions(request: HttpRequest):
    rows = OffsetSubmission.objects.all()[:200]
    return [_to_out(r) for r in rows]


@api.get("/submissions/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def get_submission(request: HttpRequest, submission_id: int):
    try:
        row = OffsetSubmission.objects.get(pk=submission_id)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    return _to_out(row)


@api.post("/submissions", response=SubmissionOut, auth=bearer_auth)
def create_submission(request: HttpRequest, body: SubmissionIn):
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "当前账号只读，不能提交刀补")
    tool_code = body.tool_code.strip()
    if not tool_code:
        raise HttpError(400, "刀具编号不能为空")
    row = OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=body.offset_um,
        submitted_by=user,
        status=OffsetSubmission.Status.PENDING,
    )
    return _to_out(row)


# --------------------------------------------------------------------------- #
# 冻结归档台
# --------------------------------------------------------------------------- #
@api.get("/archive/packages", response=list[PackageOut], auth=bearer_auth)
def list_packages(request: HttpRequest):
    return [_package_dict(p) for p in _package_qs()]


@api.post("/archive/packages", response=PackageDetailOut, auth=bearer_auth)
def create_package(request: HttpRequest, body: PackageCreateIn):
    user = require_writer(request)
    package = _guard_archive(services.create_package, body.name, user)
    package.queued_count = 0
    package.signed_count = 0
    return _package_dict(package, entries=[])


@api.get("/archive/packages/{package_id}", response=PackageDetailOut, auth=bearer_auth)
def get_package(request: HttpRequest, package_id: int):
    try:
        package = _package_qs().get(pk=package_id)
    except ArchivePackage.DoesNotExist:
        raise HttpError(404, "归档包不存在")
    entries = (
        ArchiveEntry.objects.select_related("submission")
        .filter(package_id=package_id)
        .order_by("state", "id")
    )
    return _package_dict(package, entries=entries)


@api.post("/archive/packages/{package_id}/seal", response=PackageDetailOut, auth=bearer_auth)
def seal_package(request: HttpRequest, package_id: int):
    require_writer(request)
    package = _guard_archive(services.seal_package, package_id)
    package = _package_qs().get(pk=package.id)
    entries = (
        ArchiveEntry.objects.select_related("submission")
        .filter(package_id=package_id)
        .order_by("state", "id")
    )
    return _package_dict(package, entries=entries)


@api.post("/archive/packages/{package_id}/entries", response=PackageDetailOut, auth=bearer_auth)
def queue_entry(request: HttpRequest, package_id: int, body: EntryQueueIn):
    require_writer(request)
    try:
        package = ArchivePackage.objects.get(pk=package_id)
        submission = OffsetSubmission.objects.get(pk=body.submission_id)
    except ObjectDoesNotExist:
        raise HttpError(404, "归档包或刀补记录不存在")
    _guard_archive(services.queue_entry, package, submission)
    return get_package(request, package_id)


@api.post("/archive/entries/{entry_id}/sign", response=ArchiveEntryOut, auth=bearer_auth)
def sign_entry(request: HttpRequest, entry_id: int):
    require_writer(request)
    entry = _guard_archive(services.sign_entry, entry_id)
    entry = (
        ArchiveEntry.objects.select_related("submission").get(pk=entry.id)
    )
    return _entry_dict(entry)


@api.delete("/archive/entries/{entry_id}", response=PackageOut, auth=bearer_auth)
def remove_entry(request: HttpRequest, entry_id: int):
    require_writer(request)
    try:
        entry = ArchiveEntry.objects.select_related("package").get(pk=entry_id)
    except ArchiveEntry.DoesNotExist:
        raise HttpError(404, "栏目不存在")
    package_id = entry.package_id
    _guard_archive(services.remove_queued_entry, entry_id)
    package = _package_qs().get(pk=package_id)
    return _package_dict(package)
