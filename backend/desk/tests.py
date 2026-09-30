"""冻结台 / 只读归档包验收测试。

对应验收口径：
1. 操作员一点签发，刀号/刀补/判定/理由冻进包；
2. 签发后人为改库或重算，包内三字段停在签发那一版，总览可已变；
3. 包内字段禁止任何写接口改写（API 无写口 + ORM 拦截 + 直连 SQL 由触发器拦）；
4. 观察账号可以翻包但不能点签发。
"""

import json

from django.test import Client, TestCase

from desk.auth_utils import hash_password
from desk.models import (
    ArchiveEntry,
    ArchivePackage,
    ArchiveReadOnlyError,
    OffsetSubmission,
    User,
)
from desk.services import apply_verdict, sign_submission


def make_user(username, role, password="x" * 12):
    return User.objects.create(
        username=username,
        role=role,
        password=hash_password(password),
        is_active=True,
    )


def make_done_submission(tool_code, offset_um, submitted_by=None):
    row = OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=offset_um,
        status=OffsetSubmission.Status.PENDING,
        submitted_by=submitted_by,
    )
    apply_verdict(row)
    row.refresh_from_db()
    return row


class ApiClientMixin:
    def login(self, username, password):
        res = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )
        assert res.status_code == 200, res.content
        return res.json()["token"]

    def api(self, method, path, token=None, body=None):
        kwargs = {"content_type": "application/json"}
        if token:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if body is not None:
            kwargs["data"] = json.dumps(body)
        return getattr(self.client, method)(f"/api{path}", **kwargs)


class ArchiveSignFlowTest(TestCase, ApiClientMixin):
    def setUp(self):
        self.machinist = make_user("machinist", User.Role.MACHINIST, "machine123456")
        self.auditor = make_user("auditor", User.Role.AUDITOR, "audit123456")
        self.sub1 = make_done_submission("T01", 5, self.machinist)
        self.sub2 = make_done_submission("T09", 20, self.machinist)
        self.token = self.login("machinist", "machine123456")

    def test_queue_lists_done_unsigned_only(self):
        res = self.api("get", "/archive/queue", self.token)
        self.assertEqual(res.status_code, 200)
        ids = {r["id"] for r in res.json()}
        self.assertEqual(ids, {self.sub1.id, self.sub2.id})

    def test_sign_freezes_four_fields_into_package(self):
        res = self.api(
            "post",
            "/archive/sign",
            self.token,
            {"submission_id": self.sub1.id, "reason": "首件确认"},
        )
        self.assertEqual(res.status_code, 200, res.content)
        payload = res.json()
        self.assertEqual(payload["tool_code"], "T01")
        self.assertEqual(payload["offset_um"], 5)
        self.assertEqual(payload["verdict"], "合格")
        self.assertEqual(payload["reason"], "首件确认")
        self.assertEqual(payload["signed_by_name"], "machinist")
        # 签完即出队
        queue_ids = {r["id"] for r in self.api("get", "/archive/queue", self.token).json()}
        self.assertEqual(queue_ids, {self.sub2.id})

    def test_sign_default_reason_when_blank(self):
        res = self.api("post", "/archive/sign", self.token, {"submission_id": self.sub1.id})
        self.assertEqual(res.status_code, 200)
        self.assertIn("5µm", res.json()["reason"])
        self.assertIn("合格", res.json()["reason"])

    def test_sign_two_then_tamper_live_data_package_stays_frozen(self):
        """先签两笔，再人为改库/重算：包内停在签发当刻，总览可已变。"""
        self.api("post", "/archive/sign", self.token, {"submission_id": self.sub1.id})
        self.api("post", "/archive/sign", self.token, {"submission_id": self.sub2.id})

        # 人为改库：直接改现行行的刀补与判定
        OffsetSubmission.objects.filter(pk=self.sub1.id).update(
            offset_um=99, verdict=OffsetSubmission.Verdict.FAIL
        )
        # 重算：另一行回到待复核，由 worker 逻辑重新判定
        OffsetSubmission.objects.filter(pk=self.sub2.id).update(
            offset_um=3,
            status=OffsetSubmission.Status.PENDING,
            verdict="",
        )
        apply_verdict(OffsetSubmission.objects.get(pk=self.sub2.id))

        # 总览已变
        overview = {r["id"]: r for r in self.api("get", "/submissions", self.token).json()}
        self.assertEqual(overview[self.sub1.id]["offset_um"], 99)
        self.assertEqual(overview[self.sub2.id]["offset_um"], 3)

        # 包内仍停在签发那一版
        packages = self.api("get", "/archive/packages", self.token).json()
        self.assertEqual(len(packages), 2)
        for pkg in packages:
            detail = self.api("get", f"/archive/packages/{pkg['id']}", self.token).json()
            entry = detail["entries"][0]
            if entry["submission_id"] == self.sub1.id:
                self.assertEqual((entry["tool_code"], entry["offset_um"], entry["verdict"]), ("T01", 5, "合格"))
                self.assertEqual(entry["current_offset_um"], 99)
                self.assertTrue(entry["drifted"])
            else:
                self.assertEqual((entry["tool_code"], entry["offset_um"], entry["verdict"]), ("T09", 20, "超差"))
                self.assertEqual(entry["current_offset_um"], 3)
                self.assertTrue(entry["drifted"])

    def test_sign_rejects_pending_submission(self):
        row = OffsetSubmission.objects.create(
            tool_code="T77", offset_um=1, status=OffsetSubmission.Status.PENDING
        )
        res = self.api("post", "/archive/sign", self.token, {"submission_id": row.id})
        self.assertEqual(res.status_code, 409)
        self.assertIn("结清", res.json()["detail"])

    def test_sign_rejects_duplicate(self):
        body = {"submission_id": self.sub1.id}
        self.assertEqual(self.api("post", "/archive/sign", self.token, body).status_code, 200)
        res = self.api("post", "/archive/sign", self.token, body)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(ArchiveEntry.objects.count(), 1)

    def test_sign_rejects_missing_submission(self):
        res = self.api("post", "/archive/sign", self.token, {"submission_id": 99999})
        self.assertEqual(res.status_code, 409)

    def test_auditor_can_browse_but_cannot_sign(self):
        self.api("post", "/archive/sign", self.token, {"submission_id": self.sub1.id})
        auditor_token = self.login("auditor", "audit123456")

        # 可以翻包
        for path in ("/archive/queue", "/archive/entries", "/archive/packages"):
            res = self.api("get", path, auditor_token)
            self.assertEqual(res.status_code, 200, f"{path}: {res.content}")
        pkg_id = self.api("get", "/archive/packages", auditor_token).json()[0]["id"]
        res = self.api("get", f"/archive/packages/{pkg_id}", auditor_token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["entries"][0]["offset_um"], 5)

        # 不能点签发
        res = self.api("post", "/archive/sign", auditor_token, {"submission_id": self.sub2.id})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(ArchiveEntry.objects.count(), 1)

    def test_archive_requires_auth(self):
        self.assertEqual(Client().get("/api/archive/packages").status_code, 401)
        self.assertEqual(Client().get("/api/archive/queue").status_code, 401)


class ArchiveReadOnlyGuardTest(TestCase):
    """写口拦截：ORM 任何改写/删除包内字段的尝试都必须被拒。"""

    def setUp(self):
        self.machinist = make_user("machinist", User.Role.MACHINIST)
        self.sub = make_done_submission("T01", 5, self.machinist)
        self.entry = sign_submission(
            submission_id=self.sub.id, reason="", title="", user=self.machinist
        )
        self.package = self.entry.package

    def test_model_save_update_blocked(self):
        self.entry.offset_um = 0
        with self.assertRaises(ArchiveReadOnlyError):
            self.entry.save()
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.offset_um, 5)

    def test_model_save_update_fields_blocked(self):
        self.entry.reason = "篡改"
        with self.assertRaises(ArchiveReadOnlyError):
            self.entry.save(update_fields=["reason"])

    def test_model_delete_blocked(self):
        with self.assertRaises(ArchiveReadOnlyError):
            self.entry.delete()
        with self.assertRaises(ArchiveReadOnlyError):
            self.package.delete()

    def test_queryset_update_blocked(self):
        with self.assertRaises(ArchiveReadOnlyError):
            ArchiveEntry.objects.filter(pk=self.entry.pk).update(offset_um=0)
        with self.assertRaises(ArchiveReadOnlyError):
            ArchivePackage.objects.filter(pk=self.package.pk).update(title="篡改")

    def test_queryset_delete_blocked(self):
        with self.assertRaises(ArchiveReadOnlyError):
            ArchiveEntry.objects.all().delete()
        with self.assertRaises(ArchiveReadOnlyError):
            ArchivePackage.objects.all().delete()

    def test_bulk_update_blocked(self):
        self.entry.offset_um = 0
        with self.assertRaises(ArchiveReadOnlyError):
            ArchiveEntry.objects.bulk_update([self.entry], ["offset_um"])

    def test_package_save_blocked(self):
        self.package.title = "篡改"
        with self.assertRaises(ArchiveReadOnlyError):
            self.package.save()

    def test_api_has_no_write_endpoint_for_archive(self):
        token_user = make_user("op2", User.Role.MACHINIST, "op2345678901")
        client = Client()
        login = client.post(
            "/api/auth/login",
            data=json.dumps({"username": "op2", "password": "op2345678901"}),
            content_type="application/json",
        )
        auth = {"HTTP_AUTHORIZATION": f"Bearer {login.json()['token']}"}
        pkg_id = self.package.id
        entry_id = self.entry.id
        for method in ("put", "patch", "delete"):
            for path in (
                f"/api/archive/packages/{pkg_id}",
                f"/api/archive/entries/{entry_id}",
                "/api/archive/packages",
                "/api/archive/entries",
            ):
                res = getattr(client, method)(path, content_type="application/json", **auth)
                self.assertIn(res.status_code, (404, 405), f"{method} {path} -> {res.status_code}")
        # 包内字段确实没被任何调用改写
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.tool_code, self.entry.offset_um, self.entry.verdict), ("T01", 5, "合格"))

    def test_live_submission_delete_does_not_touch_archive(self):
        """现行记录被删，包内快照仍在（外键无约束、不联动）。"""
        entry_id = self.entry.id
        OffsetSubmission.objects.filter(pk=self.sub.id).delete()
        entry = ArchiveEntry.objects.get(pk=entry_id)
        self.assertEqual((entry.tool_code, entry.offset_um, entry.verdict), ("T01", 5, "合格"))
