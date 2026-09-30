"""归档包结构 + 数据库层只读触发器。

触发器是最后一道闸：即使有人绕过 ORM 直连数据库改写或删除归档行，
PostgreSQL 也会直接抛错。非 PostgreSQL 环境（如 SQLite 单测）自动跳过。
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.migrations.operations.base import Operation

GUARD_FUNCTION = "desk_archive_readonly_guard"

CREATE_SQL = f"""
CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '归档表只读：包内字段自签发当刻冻结，禁止改写或删除 (archive read-only)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS desk_archive_package_readonly ON desk_archive_package;
CREATE TRIGGER desk_archive_package_readonly
    BEFORE UPDATE OR DELETE ON desk_archive_package
    FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}();

DROP TRIGGER IF EXISTS desk_archive_entry_readonly ON desk_archive_entry;
CREATE TRIGGER desk_archive_entry_readonly
    BEFORE UPDATE OR DELETE ON desk_archive_entry
    FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}();
"""

DROP_SQL = f"""
DROP TRIGGER IF EXISTS desk_archive_package_readonly ON desk_archive_package;
DROP TRIGGER IF EXISTS desk_archive_entry_readonly ON desk_archive_entry;
DROP FUNCTION IF EXISTS {GUARD_FUNCTION}();
"""


class CreateArchiveReadOnlyTriggers(Operation):
    """PostgreSQL 专用的只读触发器；其他数据库静默跳过。"""

    reversible = True
    reduces_to_sql = False

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(CREATE_SQL)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(DROP_SQL)

    def describe(self):
        return "Create read-only triggers on archive tables (PostgreSQL only)"


class Migration(migrations.Migration):
    dependencies = [
        ("desk", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArchivePackage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=64)),
                ("created_by_name", models.CharField(max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "desk_archive_package",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="ArchiveEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("tool_code", models.CharField(max_length=32)),
                ("offset_um", models.IntegerField()),
                ("verdict", models.CharField(max_length=8)),
                ("reason", models.TextField(blank=True, default="")),
                ("signed_by_name", models.CharField(max_length=150)),
                ("signed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "package",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="desk.archivepackage",
                    ),
                ),
                (
                    "submission",
                    models.OneToOneField(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="archive_entry",
                        to="desk.offsetsubmission",
                    ),
                ),
            ],
            options={
                "db_table": "desk_archive_entry",
                "ordering": ["-signed_at", "-id"],
            },
        ),
        CreateArchiveReadOnlyTriggers(),
    ]
