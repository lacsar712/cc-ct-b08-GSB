"""只读归档包验收测试。

覆盖题目关键判分点：
- 冻结台左排队待签、右已签；签发把刀号/刀补/判定/理由冻进包；
- 总览现行值随后续判定可变，包内三字段停在签发当刻；
- 已签/已封包字段禁止任何写口（HTTP、ORM save、queryset.update、delete）；
- 观察账号可翻包但不能签发；
- 先签两笔、再改库/重算一行后重开包：快照不变、总览已变、对拍有差。
"""

from django.core.exceptions import ValidationError
from django.test import TestCase
from ninja.testing import TestClient

from desk.api import api
from desk.auth_utils import create_access_token
from desk.models import (
    ArchiveEntry,
    ArchiveError,
    ArchivePackage,
    OffsetSubmission,
    User,
)
from desk import services


class ArchiveDeskTests(TestCase):
    def setUp(self):
        self.client = TestClient(api)
        self.machinist = User.objects.create_user(
            username="machinist", password="x", role=User.Role.MACHINIST
        )
        self.auditor = User.objects.create_user(
            username="auditor", password="x", role=User.Role.AUDITOR
        )
        self.mt = create_access_token(self.machinist)
        self.at = create_access_token(self.auditor)
        self.mauth = {"Authorization": f"Bearer {self.mt}"}
        self.ath = {"Authorization": f"Bearer {self.at}"}

        self.s1 = OffsetSubmission.objects.create(
            tool_code="T01", offset_um=5,
            status=OffsetSubmission.Status.DONE,
            verdict=OffsetSubmission.Verdict.PASS,
        )
        self.s2 = OffsetSubmission.objects.create(
            tool_code="T09", offset_um=20,
            status=OffsetSubmission.Status.DONE,
            verdict=OffsetSubmission.Verdict.FAIL,
        )

    # ---- 主流程：建包→入队→签两笔→封包 ----------------------------- #
    def test_sign_two_then_seal_freezes_snapshot(self):
        r = self.client.post("/archive/packages", json={"name": "批次甲"}, headers=self.mauth)
        self.assertEqual(r.status_code, 200, r.content)
        pkg = r.json()
        pid = pkg["id"]
        self.assertEqual(pkg["status"], "open")

        for sid in (self.s1.id, self.s2.id):
            r = self.client.post(
                f"/archive/packages/{pid}/entries",
                json={"submission_id": sid}, headers=self.mauth,
            )
            self.assertEqual(r.status_code, 200, r.content)

        r = self.client.get(f"/archive/packages/{pid}", headers=self.mauth)
        entries = r.json()["entries"]
        queued = [e for e in entries if e["state"] == "queued"]
        self.assertEqual(len(queued), 2)
        # 待签时不持有冻结快照
        self.assertIsNone(queued[0]["snap_offset_um"])

        for e in entries:
            r = self.client.post(f"/archive/entries/{e['id']}/sign", headers=self.mauth)
            self.assertEqual(r.status_code, 200, r.content)
            signed = r.json()
            self.assertEqual(signed["state"], "signed")
            self.assertFalse(signed["drift"])

        r = self.client.post(f"/archive/packages/{pid}/seal", headers=self.mauth)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "sealed")

        e1 = ArchiveEntry.objects.get(submission_id=self.s1.id)
        self.assertEqual(e1.snap_tool_code, "T01")
        self.assertEqual(e1.snap_offset_um, 5)
        self.assertEqual(e1.snap_verdict, "合格")
        self.assertIn("合格", e1.snap_reason)
        self.assertIsNotNone(e1.signed_at)

    # ---- 观察账号：可翻包，不能签发/任何写 --------------------------- #
    def test_auditor_can_read_but_not_write(self):
        pkg = services.create_package("批次乙", self.machinist)
        services.queue_entry(pkg, self.s1)

        self.assertEqual(
            self.client.get("/archive/packages", headers=self.ath).status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/archive/packages/{pkg.id}", headers=self.ath).status_code, 200
        )
        for method, path, body in [
            ("post", "/archive/packages", {"name": "x"}),
            ("post", f"/archive/packages/{pkg.id}/entries", {"submission_id": self.s2.id}),
            ("post", f"/archive/packages/{pkg.id}/seal", None),
            ("post", f"/archive/entries/{pkg.entries.first().id}/sign", None),
        ]:
            r = getattr(self.client, method)(path, json=body, headers=self.ath)
            self.assertEqual(r.status_code, 403, (method, path, r.content))

    # ---- 核心：签两笔后改库/重算，包内定格、总览可变、对拍有差 ------- #
    def test_tamper_after_sign_snapshot_stays_overview_moves(self):
        pkg = services.create_package("批次丙", self.machinist)
        e1 = services.queue_entry(pkg, self.s1)
        e2 = services.queue_entry(pkg, self.s2)
        e1 = services.sign_entry(e1.id)
        e2 = services.sign_entry(e2.id)
        services.seal_package(pkg.id)

        frozen_offset, frozen_verdict = e1.snap_offset_um, e1.snap_verdict
        frozen_reason = e1.snap_reason

        # 人为改库 / 重算其中一行刀补：总览现行数字允许跟着变
        self.s1.offset_um = 40
        self.s1.verdict = OffsetSubmission.Verdict.FAIL
        self.s1.save(update_fields=["offset_um", "verdict"])

        # 总览现行值已变
        cur = self.client.get(f"/submissions/{self.s1.id}", headers=self.mauth).json()
        self.assertEqual(cur["offset_um"], 40)
        self.assertEqual(cur["verdict"], "超差")

        # 重开包：包内三字段 + 理由停在签发当刻，对拍出现差异
        r = self.client.get(f"/archive/packages/{pkg.id}", headers=self.ath)
        d = {e["submission_id"]: e for e in r.json()["entries"]}
        snap = d[self.s1.id]
        self.assertEqual(snap["snap_offset_um"], frozen_offset)
        self.assertEqual(snap["snap_verdict"], frozen_verdict)
        self.assertEqual(snap["snap_reason"], frozen_reason)
        self.assertEqual(snap["live_offset_um"], 40)
        self.assertEqual(snap["live_verdict"], "超差")
        self.assertTrue(snap["drift"])
        # 另一笔未改，不漂移
        self.assertFalse(d[self.s2.id]["drift"])

        e1.refresh_from_db()
        self.assertEqual(e1.snap_offset_um, frozen_offset)
        self.assertEqual(e1.snap_verdict, frozen_verdict)

    # ---- 写口封堵：HTTP / ORM / queryset / 删除 ---------------------- #
    def test_all_write_mouths_blocked_for_signed_and_sealed(self):
        pkg = services.create_package("批次丁", self.machinist)
        e1 = services.queue_entry(pkg, self.s1)
        services.sign_entry(e1.id)
        services.seal_package(pkg.id)

        # 重复签发被拦
        r = self.client.post(f"/archive/entries/{e1.id}/sign", headers=self.mauth)
        self.assertEqual(r.status_code, 409)

        # 不存在 PATCH/PUT 改栏目接口
        for method in ("patch", "put"):
            r = getattr(self.client, method)(
                f"/archive/entries/{e1.id}", json={"snap_offset_um": 99},
                headers=self.mauth,
            )
            self.assertIn(r.status_code, (404, 405))

        # 已封包不能再加栏目
        r = self.client.post(
            f"/archive/packages/{pkg.id}/entries",
            json={"submission_id": self.s2.id}, headers=self.mauth,
        )
        self.assertEqual(r.status_code, 409)

        # ORM 实例改写冻结列 —— save 与 save(update_fields=...) 都拦
        e1.refresh_from_db()
        e1.snap_offset_um = 99
        with self.assertRaises(ArchiveError):
            e1.save()
        with self.assertRaises(ArchiveError):
            e1.save(update_fields=["snap_offset_um"])
        e1.snap_verdict = "超差"
        with self.assertRaises(ArchiveError):
            e1.save()
        e1.snap_reason = "被篡改的理由"
        with self.assertRaises(ArchiveError):
            e1.save(update_fields=["snap_reason"])
        # 状态回退也拦
        e1.state = ArchiveEntry.State.QUEUED
        with self.assertRaises(ArchiveError):
            e1.save(update_fields=["state"])

        # queryset 批量写口
        with self.assertRaises(ArchiveError):
            ArchiveEntry.objects.filter(pk=e1.id).update(snap_offset_um=123)
        with self.assertRaises(ArchiveError):
            ArchiveEntry.objects.filter(pk=e1.id).delete()
        with self.assertRaises(ArchiveError):
            e1.delete()

        # 包自身只读：改名 / 解封 / 删除
        pkg.name = "被改名"
        with self.assertRaises(ArchiveError):
            pkg.save(update_fields=["name"])
        pkg.status = ArchivePackage.Status.OPEN
        with self.assertRaises(ArchiveError):
            pkg.save(update_fields=["status"])
        with self.assertRaises(ArchiveError):
            pkg.delete()
        with self.assertRaises(ArchiveError):
            ArchivePackage.objects.filter(pk=pkg.id).update(name="x")

        # 库内冻结值确实未被任何尝试改动
        e1.refresh_from_db()
        self.assertEqual(e1.snap_offset_um, 5)
        self.assertEqual(e1.snap_verdict, "合格")

    # ---- 打开包的待签可移除，已签不能移除 ----------------------------- #
    def test_queued_removable_signed_not(self):
        pkg = services.create_package("批次戊", self.machinist)
        eq = services.queue_entry(pkg, self.s1)
        es = services.queue_entry(pkg, self.s2)
        services.sign_entry(es.id)

        services.remove_queued_entry(eq.id)  # 待签可移除
        with self.assertRaises(ArchiveError):
            services.remove_queued_entry(es.id)  # 已签不可移除

        # 还有待签时不能封包
        pkg2 = services.create_package("批次己", self.machinist)
        services.queue_entry(pkg2, self.s1)
        with self.assertRaises(ArchiveError):
            services.seal_package(pkg2.id)

    # ---- 原生 SQL 直接改库/重算源表：包内独立快照纹丝不动 ------------ #
    def test_raw_sql_tamper_of_source_does_not_touch_snapshot(self):
        from django.db import connection

        pkg = services.create_package("批次庚", self.machinist)
        e1 = services.queue_entry(pkg, self.s1)
        e1 = services.sign_entry(e1.id)
        frozen = (e1.snap_tool_code, e1.snap_offset_um, e1.snap_verdict, e1.snap_reason)

        # 绕过应用，用裸 SQL 直接 UPDATE 源刀补表（模拟 DBA 人为改库/重算）。
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE desk_offsetsubmission SET offset_um = 88, verdict = %s "
                "WHERE id = %s",
                [OffsetSubmission.Verdict.FAIL, self.s1.id],
            )

        # 应用层重开包：快照停在签发当刻；现行值已变；对拍有差。
        r = self.client.get(f"/archive/packages/{pkg.id}", headers=self.ath).json()
        snap = next(e for e in r["entries"] if e["submission_id"] == self.s1.id)
        self.assertEqual(
            (snap["snap_tool_code"], snap["snap_offset_um"], snap["snap_verdict"],
             snap["snap_reason"]),
            frozen,
        )
        self.assertEqual(snap["live_offset_um"], 88)
        self.assertEqual(snap["live_verdict"], "超差")
        self.assertTrue(snap["drift"])

    # ---- bulk_update 批量写口同样被拦 -------------------------------- #
    def test_bulk_update_blocked(self):
        pkg = services.create_package("批次辛", self.machinist)
        e1 = services.queue_entry(pkg, self.s1)
        services.sign_entry(e1.id)

        e1.snap_offset_um = 123
        with self.assertRaises(ArchiveError):
            ArchiveEntry.objects.bulk_update([e1], ["snap_offset_um"])

        e1.refresh_from_db()
        self.assertEqual(e1.snap_offset_um, 5)
