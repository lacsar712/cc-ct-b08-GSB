from datetime import datetime
from typing import Optional

from django.db import models
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import ArchiveEntry, ArchivePackage, OffsetSubmission, User
from desk.services import ArchiveSignError, sign_submission

api = NinjaAPI(title="数控刀补复核台", version="1.0")


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


# ---------- 只读归档包（冻结台） ----------


class ArchiveSignIn(Schema):
    submission_id: int
    reason: str = ""
    title: str = ""


class ArchiveQueueItemOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    verdict: str
    reviewed_at: Optional[datetime]


class ArchiveEntryOut(Schema):
    id: int
    package_id: int
    submission_id: Optional[int]
    tool_code: str
    offset_um: int
    verdict: str
    reason: str
    signed_by_name: str
    signed_at: datetime


class ArchiveEntryWithCurrentOut(ArchiveEntryOut):
    """包内冻结字段 + 复核总览现行读数对拍。"""

    current_tool_code: Optional[str]
    current_offset_um: Optional[int]
    current_verdict: Optional[str]
    current_status: Optional[str]
    drifted: bool


class ArchivePackageOut(Schema):
    id: int
    title: str
    created_by_name: str
    created_at: datetime
    entry_count: int


class ArchivePackageDetailOut(Schema):
    id: int
    title: str
    created_by_name: str
    created_at: datetime
    entries: list[ArchiveEntryWithCurrentOut]


def _entry_to_out(entry: ArchiveEntry) -> ArchiveEntryOut:
    return ArchiveEntryOut(
        id=entry.id,
        package_id=entry.package_id,
        submission_id=entry.submission_id,
        tool_code=entry.tool_code,
        offset_um=entry.offset_um,
        verdict=entry.verdict,
        reason=entry.reason,
        signed_by_name=entry.signed_by_name,
        signed_at=entry.signed_at,
    )


def _entry_with_current(
    entry: ArchiveEntry, current: Optional[OffsetSubmission]
) -> ArchiveEntryWithCurrentOut:
    drifted = (
        current is None
        or current.tool_code != entry.tool_code
        or current.offset_um != entry.offset_um
        or current.verdict != entry.verdict
    )
    return ArchiveEntryWithCurrentOut(
        **_entry_to_out(entry).model_dump(),
        current_tool_code=current.tool_code if current else None,
        current_offset_um=current.offset_um if current else None,
        current_verdict=(current.verdict or "") if current else None,
        current_status=current.status if current else None,
        drifted=drifted,
    )


@api.get("/archive/queue", response=list[ArchiveQueueItemOut], auth=bearer_auth)
def archive_queue(request: HttpRequest):
    """排队待签：已结清且尚未签发的刀补。"""
    rows = OffsetSubmission.objects.filter(
        status=OffsetSubmission.Status.DONE,
        archive_entry__isnull=True,
    ).order_by("reviewed_at", "id")[:200]
    return [
        ArchiveQueueItemOut(
            id=r.id,
            tool_code=r.tool_code,
            offset_um=r.offset_um,
            verdict=r.verdict or "",
            reviewed_at=r.reviewed_at,
        )
        for r in rows
    ]


@api.get("/archive/entries", response=list[ArchiveEntryOut], auth=bearer_auth)
def archive_entries(request: HttpRequest):
    """已签条目：全部冻结快照，只读。"""
    return [_entry_to_out(e) for e in ArchiveEntry.objects.all()[:500]]


@api.get("/archive/packages", response=list[ArchivePackageOut], auth=bearer_auth)
def archive_packages(request: HttpRequest):
    packages = list(ArchivePackage.objects.all()[:200])
    counts = {
        row["package_id"]: row["n"]
        for row in ArchiveEntry.objects.filter(
            package_id__in=[p.id for p in packages]
        )
        .values("package_id")
        .annotate(n=models.Count("id"))
    }
    return [
        ArchivePackageOut(
            id=p.id,
            title=p.title,
            created_by_name=p.created_by_name,
            created_at=p.created_at,
            entry_count=counts.get(p.id, 0),
        )
        for p in packages
    ]


@api.get(
    "/archive/packages/{package_id}",
    response=ArchivePackageDetailOut,
    auth=bearer_auth,
)
def archive_package_detail(request: HttpRequest, package_id: int):
    """打开归档包：包内栏目冻结在签发当刻，并附现行读数对拍。"""
    try:
        package = ArchivePackage.objects.get(pk=package_id)
    except ArchivePackage.DoesNotExist:
        raise HttpError(404, "归档包不存在")
    entries = list(package.entries.all())
    current_by_id = {
        s.id: s
        for s in OffsetSubmission.objects.filter(
            id__in=[e.submission_id for e in entries if e.submission_id]
        )
    }
    return ArchivePackageDetailOut(
        id=package.id,
        title=package.title,
        created_by_name=package.created_by_name,
        created_at=package.created_at,
        entries=[
            _entry_with_current(e, current_by_id.get(e.submission_id))
            for e in entries
        ],
    )


@api.post("/archive/sign", response=ArchiveEntryOut, auth=bearer_auth)
def archive_sign(request: HttpRequest, body: ArchiveSignIn):
    """签发：操作员一点，刀号/刀补/判定/理由即刻冻进只读归档包。"""
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "观察账号只读，不能签发归档包")
    try:
        entry = sign_submission(
            submission_id=body.submission_id,
            reason=body.reason,
            title=body.title,
            user=user,
        )
    except ArchiveSignError as exc:
        raise HttpError(409, str(exc)) from exc
    return _entry_to_out(entry)
