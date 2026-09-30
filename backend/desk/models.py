from django.contrib.auth.models import AbstractUser
from django.db import models


class ArchiveReadOnlyError(PermissionError):
    """归档数据只读：任何写接口都不得改写包内字段。"""


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


class ReadOnlyArchiveQuerySet(models.QuerySet):
    """归档查询集：堵死 ORM 批量写口（update/delete/bulk_update）。"""

    def update(self, **kwargs):
        raise ArchiveReadOnlyError("归档数据只读，禁止通过写接口改写包内字段")

    def delete(self):
        raise ArchiveReadOnlyError("归档数据只读，禁止通过写接口删除归档记录")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ArchiveReadOnlyError("归档数据只读，禁止通过写接口改写包内字段")


class ReadOnlyArchiveModel(models.Model):
    """归档模型基类：只允许签发当刻的 INSERT，之后任何 save/delete 一律拒绝。

    与数据库层的只读触发器（见迁移 0002）互为双保险：
    ORM 写口在这里拦，绕过 ORM 的直连 SQL 由触发器拦。
    """

    objects = ReadOnlyArchiveQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ArchiveReadOnlyError("归档数据只读，签发后禁止改写包内字段")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ArchiveReadOnlyError("归档数据只读，签发后禁止删除归档记录")


class ArchivePackage(ReadOnlyArchiveModel):
    """只读归档包：签发即封存，包本身与包内条目都不再可写。"""

    title = models.CharField(max_length=64)
    created_by_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "desk_archive_package"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.title} (#{self.pk})"


class ArchiveEntry(ReadOnlyArchiveModel):
    """包内条目：刀号/刀补/判定/理由四字段冻结在签发那一版。

    submission 仅作历史溯源引用（无数据库外键约束、删除现行记录不联动），
    包内读数完全来自本表快照，不随复核总览的现行数据变化。
    """

    package = models.ForeignKey(
        ArchivePackage,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    submission = models.OneToOneField(
        OffsetSubmission,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="archive_entry",
    )
    tool_code = models.CharField(max_length=32)
    offset_um = models.IntegerField()
    verdict = models.CharField(max_length=8)
    reason = models.TextField(blank=True, default="")
    signed_by_name = models.CharField(max_length=150)
    signed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "desk_archive_entry"
        ordering = ["-signed_at", "-id"]

    def __str__(self) -> str:
        return f"{self.tool_code} {self.offset_um}µm {self.verdict}（已签发）"
