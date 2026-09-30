from django.db import migrations, models
import django.db.models.deletion


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
                ("name", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "冻结台（打开）"),
                            ("sealed", "已封只读归档包"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sealed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="archive_packages",
                        to="desk.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
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
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "排队待签"),
                            ("signed", "已签（包内栏目）"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "snap_tool_code",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                ("snap_offset_um", models.IntegerField(blank=True, null=True)),
                ("snap_verdict", models.CharField(blank=True, default="", max_length=8)),
                ("snap_reason", models.CharField(blank=True, default="", max_length=255)),
                ("signed_at", models.DateTimeField(blank=True, null=True)),
                ("queued_at", models.DateTimeField(auto_now_add=True)),
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
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="archive_entries",
                        to="desk.offsetsubmission",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.AddConstraint(
            model_name="archiveentry",
            constraint=models.UniqueConstraint(
                fields=("package", "submission"),
                name="uniq_archive_entry_per_package_submission",
            ),
        ),
    ]
