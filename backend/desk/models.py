from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        MACHINIST = "machinist", "操作员"
        AUDITOR = "auditor", "复核员"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MACHINIST,
    )

    @property
    def can_write(self) -> bool:
        return self.role == self.Role.MACHINIST


class OffsetSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待复核"
        PROCESSING = "processing", "复核中"
        DONE = "done", "已完成"

    class Verdict(models.TextChoices):
        PASS = "合格", "合格"
        FAIL = "超差", "超差"

    tool_code = models.CharField(max_length=32, db_index=True)
    offset_um = models.IntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    verdict = models.CharField(
        max_length=8,
        choices=Verdict.choices,
        blank=True,
        default="",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tool_code} {self.offset_um}µm"


# 签发当刻冻结、此后任何写口都不得改写的列（刀号 / 刀补 / 判定 / 理由 / 签发时刻）。
FROZEN_ENTRY_FIELDS = (
    "snap_tool_code",
    "snap_offset_um",
    "snap_verdict",
    "snap_reason",
    "signed_at",
)

# 已签条目自身受保护、不允许再变动的列（状态、所属包、来源记录一并锁定）。
LOCKED_ENTRY_FIELDS = ("state", "package_id", "submission_id") + FROZEN_ENTRY_FIELDS


class ArchiveError(ValidationError):
    """归档只读规则被违反。"""


class ArchiveEntryQuerySet(models.QuerySet):
    """查询集层写口保护：拦住绕过实例 save()/delete() 的批量写口。"""

    def update(self, **kwargs):
        self._guard_write(kwargs.keys())
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        self._guard_write(fields)
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        # 已签条目（以及已封包内条目）不允许批量删除。
        self._guard_write(["__delete__"])
        return super().delete()

    def _guard_write(self, fields):
        protected = self.filter(
            models.Q(state=ArchiveEntry.State.SIGNED)
            | models.Q(package__status=ArchivePackage.Status.SEALED)
        )
        touching = set(fields)
        if "__delete__" in touching or touching.intersection(LOCKED_ENTRY_FIELDS):
            if protected.exists():
                raise ArchiveError(
                    "包内栏目为只读归档，禁止对已签或已封包条目执行该写操作"
                )


class ArchivePackageQuerySet(models.QuerySet):
    """已封包为只读归档：拦住批量改名/解封/删除。"""

    _SEALED_LOCKED = ("status", "name", "sealed_at")

    def update(self, **kwargs):
        if set(kwargs).intersection(self._SEALED_LOCKED) and self.filter(
            status=ArchivePackage.Status.SEALED
        ).exists():
            raise ArchiveError("归档包已封只读，禁止改写")
        return super().update(**kwargs)

    def delete(self):
        if self.filter(status=ArchivePackage.Status.SEALED).exists():
            raise ArchiveError("归档包已封只读，禁止删除")
        if self.filter(entries__state=ArchiveEntry.State.SIGNED).exists():
            raise ArchiveError("包内已有签发栏目，属归档记录，禁止整包删除")
        return super().delete()


class ArchivePackage(models.Model):
    """只读归档包：打开时是冻结台，封包后整包只读。"""

    class Status(models.TextChoices):
        OPEN = "open", "冻结台（打开）"
        SEALED = "sealed", "已封只读归档包"

    objects = ArchivePackageQuerySet.as_manager()

    name = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archive_packages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name}（{self.get_status_display()}）"

    @property
    def is_sealed(self) -> bool:
        return self.status == self.Status.SEALED

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("status", "name", "sealed_at")
                .first()
            )
            # 封包当刻允许 open→sealed；已封后禁止改名/解封/改封包时刻。
            if old and old["status"] == self.Status.SEALED:
                changed = [
                    f
                    for f in ("status", "name", "sealed_at")
                    if getattr(self, f) != old[f]
                ]
                if changed:
                    raise ArchiveError(
                        "归档包已封只读，禁止改写：" + "、".join(changed)
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk is not None:
            if type(self).objects.filter(
                pk=self.pk, status=self.Status.SEALED
            ).exists():
                raise ArchiveError("归档包已封只读，禁止删除")
            if ArchiveEntry.objects.filter(
                package_id=self.pk, state=ArchiveEntry.State.SIGNED
            ).exists():
                raise ArchiveError("包内已有签发栏目，属归档记录，禁止整包删除")
        super().delete(*args, **kwargs)


class ArchiveEntry(models.Model):
    """归档条目。排队待签时不持有快照；签发当刻冻结刀号/刀补/判定/理由。"""

    class State(models.TextChoices):
        QUEUED = "queued", "排队待签"
        SIGNED = "signed", "已签（包内栏目）"

    objects = ArchiveEntryQuerySet.as_manager()

    package = models.ForeignKey(
        ArchivePackage,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    submission = models.ForeignKey(
        OffsetSubmission,
        on_delete=models.PROTECT,
        related_name="archive_entries",
    )
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.QUEUED,
        db_index=True,
    )

    # 签发当刻冻结；签发前为空，签发后任何写口都不得改写。
    snap_tool_code = models.CharField(max_length=32, blank=True, default="")
    snap_offset_um = models.IntegerField(null=True, blank=True)
    snap_verdict = models.CharField(max_length=8, blank=True, default="")
    snap_reason = models.CharField(max_length=255, blank=True, default="")
    signed_at = models.DateTimeField(null=True, blank=True)

    queued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            # 同一刀补记录在同一包内只能出现一次。
            models.UniqueConstraint(
                fields=["package", "submission"],
                name="uniq_archive_entry_per_package_submission",
            )
        ]

    @property
    def is_signed(self) -> bool:
        return self.state == self.State.SIGNED

    def _is_protected(self, old) -> bool:
        return (
            old is not None
            and (
                old["state"] == self.State.SIGNED
                or old["package__status"] == ArchivePackage.Status.SEALED
            )
        )

    def save(self, *args, **kwargs):
        """实例层写口保护（覆盖 ORM .save() 与 Python 侧赋值）。

        - 新建（无 pk）：禁止写进已封包。
        - 已签 / 已封包条目：锁定列一律拒写；首次签发时旧状态为 queued，
          不触发本保护，因此只有“签发”这一受控动作能落冻结值。
        """
        if self.pk is not None:
            old = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*LOCKED_ENTRY_FIELDS, "package__status")
                .first()
            )
        else:
            old = None
            if self.package_id is not None and ArchivePackage.objects.filter(
                pk=self.package_id, status=ArchivePackage.Status.SEALED
            ).exists():
                raise ArchiveError("归档包已封只读，禁止新增栏目")

        if self._is_protected(old):
            changed = [f for f in LOCKED_ENTRY_FIELDS if getattr(self, f) != old[f]]
            if changed:
                raise ArchiveError(
                    "包内栏目为只读归档，禁止改写：" + "、".join(changed)
                )

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        old = (
            type(self)
            .objects.filter(pk=self.pk)
            .values("state", "package__status")
            .first()
        )
        if self._is_protected(old):
            raise ArchiveError("包内栏目为只读归档，禁止删除已签或已封包条目")
        super().delete(*args, **kwargs)
