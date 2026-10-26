import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app
from app import EDITORIAL_QC_CHECKS, validate_editorial_qc




class ShotstackKeySelectionTests(unittest.TestCase):
    def test_stage_uses_stage_key_before_legacy_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_PRODUCTION_API_KEY": "production-only",
        }, clear=True):
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "stage-only")

    def test_production_uses_production_key_before_legacy_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_PRODUCTION_API_KEY": "production-only",
        }, clear=True):
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "production-only")

    def test_stage_and_production_fail_closed_without_environment_specific_keys(self):
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY": "legacy"}, clear=True):
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "")
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "")

class ShotstackCredentialIsolationTests(unittest.TestCase):
    def test_stage_does_not_fall_back_to_generic_key(self):
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY": "legacy"}, clear=True):
            self.assertEqual(app._shotstack_key_source_for_base("https://api.shotstack.io/edit/stage"), "missing")
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "")

    def test_stage_uses_only_stage_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
        }, clear=True):
            self.assertEqual(app._shotstack_key_source_for_base("https://api.shotstack.io/edit/stage"), "SHOTSTACK_STAGE_API_KEY")
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "stage-only")

    def test_production_does_not_fall_back_to_generic_or_stage_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
        }, clear=True):
            self.assertEqual(app._shotstack_key_source_for_base("https://api.shotstack.io/edit/v1"), "missing")
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "")

    def test_production_uses_only_production_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
            "SHOTSTACK_PRODUCTION_API_KEY": "production-only",
        }, clear=True):
            self.assertEqual(app._shotstack_key_source_for_base("https://api.shotstack.io/edit/v1"), "SHOTSTACK_PRODUCTION_API_KEY")
            self.assertEqual(app._shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "production-only")

    def test_stage_render_endpoint_suffix_normalizes_to_stage_base(self):
        with patch.dict(os.environ, {"SHOTSTACK_STAGE_API_KEY": "stage-only"}, clear=True):
            self.assertEqual(app._shotstack_key_source_for_base("https://api.shotstack.io/edit/stage/render"), "SHOTSTACK_STAGE_API_KEY")

    def test_untrusted_bases_never_receive_generic_key(self):
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY": "legacy"}, clear=True):
            for base in (
                "https://attacker.example/edit/stage",
                "https://api.shotstack.io.evil.example/edit/stage",
                "http://api.shotstack.io/edit/stage",
                "https://api.shotstack.io/edit/custom",
                "https://api.shotstack.io/edit/stage?redirect=https://attacker.example",
                "https://api.shotstack.io:443/edit/stage",
                "https://api.shotstack.io/edit/stage#fragment",
            ):
                with self.subTest(base=base):
                    self.assertEqual(app._shotstack_key_source_for_base(base), "missing")
                    self.assertEqual(app._shotstack_key_for_base(base), "")

    def test_public_error_summary_redacts_raw_details(self):
        raw = "provider error token=synthetic-test-secret private-url=https://example.invalid/private"
        summary = app._safe_public_error_summary(raw)
        self.assertEqual(summary, "details_redacted")
        self.assertNotIn("synthetic-test-secret", summary)
        self.assertNotIn("example.invalid", summary)

    def test_public_error_summary_empty_value_is_none(self):
        self.assertIsNone(app._safe_public_error_summary(""))


class EditorialQCTests(unittest.TestCase):
    def test_approval_requires_every_check_true(self):
        checklist = {key: True for key in EDITORIAL_QC_CHECKS}
        self.assertTrue(validate_editorial_qc("approve", checklist))
        checklist[EDITORIAL_QC_CHECKS[0]] = False
        self.assertFalse(validate_editorial_qc("approve", checklist))

    def test_missing_check_fails_closed(self):
        checklist = {key: True for key in EDITORIAL_QC_CHECKS[:-1]}
        self.assertFalse(validate_editorial_qc("approve", checklist))

    def test_reject_does_not_require_approval_checklist(self):
        self.assertTrue(validate_editorial_qc("reject", {}))

    def test_unknown_decision_rejected(self):
        self.assertFalse(validate_editorial_qc("publish", {key: True for key in EDITORIAL_QC_CHECKS}))


class EditorialQCEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app.DB_PATH
        self.old_database_url = app.DATABASE_URL
        self.old_token = os.environ.get("IA10_BRIDGE_TOKEN")
        self.old_review_token = os.environ.get("IA10_REVIEW_TOKEN")
        app.DB_PATH = str(Path(self.tempdir.name) / "test.sqlite3")
        app.DATABASE_URL = ""
        os.environ["IA10_BRIDGE_TOKEN"] = "unit-test-token"
        os.environ["IA10_REVIEW_TOKEN"] = "unit-test-token"
        con = app.connect_db()
        now = 1000
        rows = [
            ("production-test", "{}", "QC_PENDING_REVIEW", now, now, "provider-123", "https://example.invalid/video.mp4", "decoded_portrait_1080x1920_duration_5.01s_audio_stream_present"),
            ("IA10_BOOTSTRAP_TEST_RENDER unit test", "{}", "QC_PENDING_REVIEW", now, now, "provider-test", "https://example.invalid/diagnostic.mp4", "diagnostic"),
        ]
        for row in rows:
            app.execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at,provider_id,render_url,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", row)
        con.commit()
        con.close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%s" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.DB_PATH = self.old_db_path
        app.DATABASE_URL = self.old_database_url
        if self.old_token is None:
            os.environ.pop("IA10_BRIDGE_TOKEN", None)
        else:
            os.environ["IA10_BRIDGE_TOKEN"] = self.old_token
        if self.old_review_token is None:
            os.environ.pop("IA10_REVIEW_TOKEN", None)
        else:
            os.environ["IA10_REVIEW_TOKEN"] = self.old_review_token
        self.tempdir.cleanup()

    def post_qc(self, job_id, payload, token="unit-test-token"):
        request = urllib.request.Request(
            self.base_url + "/jobs/%s/qc" % job_id,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def get_job_state(self, job_id):
        con = app.connect_db()
        row = app.execute(con, "SELECT state,qc_review_json,error FROM jobs WHERE id=%s", (job_id,)).fetchone()
        con.close()
        return row

    def test_unauthorized_request_is_rejected(self):
        status, body = self.post_qc(1, {"decision": "approve", "checklist": {}}, token="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_incomplete_checklist_does_not_change_state(self):
        status, body = self.post_qc(1, {"decision": "approve", "checklist": {key: True for key in EDITORIAL_QC_CHECKS[:-1]}})
        self.assertEqual(status, 400)
        self.assertEqual(self.get_job_state(1)["state"], "QC_PENDING_REVIEW")

    def test_diagnostic_job_cannot_be_approved(self):
        payload = {"decision": "approve", "checklist": {key: True for key in EDITORIAL_QC_CHECKS}}
        status, body = self.post_qc(2, payload)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "diagnostic_jobs_cannot_be_approved")
        self.assertEqual(self.get_job_state(2)["state"], "QC_PENDING_REVIEW")

    def test_rejection_requires_reason_and_marks_job_failed(self):
        status, body = self.post_qc(1, {"decision": "reject", "checklist": {}, "reason": "Fact not sourced"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "QC_FAILED")
        row = self.get_job_state(1)
        self.assertEqual(row["state"], "QC_FAILED")
        self.assertIn("editorial_qc_rejected", row["error"])

    def test_second_review_cannot_approve_job_twice(self):
        payload = {"decision": "approve", "checklist": {key: True for key in EDITORIAL_QC_CHECKS}}
        first_status, first_body = self.post_qc(1, payload)
        second_status, second_body = self.post_qc(1, payload)
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 409)
        self.assertEqual(second_body["error"], "job_not_pending_editorial_review")
        self.assertEqual(self.get_job_state(1)["state"], "READY_TO_PUBLISH")

    def test_rejection_requires_nonempty_reason(self):
        status, body = self.post_qc(1, {"decision": "reject", "checklist": {}, "reason": "  "})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "rejection_reason_required")
        self.assertEqual(self.get_job_state(1)["state"], "QC_PENDING_REVIEW")

    def test_approved_production_job_becomes_ready_but_is_not_published(self):
        payload = {"decision": "approve", "checklist": {key: True for key in EDITORIAL_QC_CHECKS}}
        status, body = self.post_qc(1, payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "READY_TO_PUBLISH")
        self.assertFalse(body["published"])
        row = self.get_job_state(1)
        self.assertEqual(row["state"], "READY_TO_PUBLISH")
        self.assertIn('"decision":"approve"', row["qc_review_json"])
        self.assertIn("decoded_portrait_1080x1920", row["error"])


    def get_review_queue(self, query="", token="unit-test-token"):
        request = urllib.request.Request(
            self.base_url + "/jobs/review-queue" + query,
            headers={"Authorization": "Bearer " + token},
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_review_queue_rejects_unauthorized_access(self):
        status, body = self.get_review_queue(token="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_review_queue_rejects_invalid_pagination(self):
        for query in ("?limit=0", "?limit=11", "?offset=-1", "?offset=10001", "?limit=bad"):
            with self.subTest(query=query):
                status, body = self.get_review_queue(query)
                self.assertEqual(status, 400)
        self.assertEqual(self.get_job_state(1)["state"], "QC_PENDING_REVIEW")

    def test_review_queue_paginates_without_approving_or_publishing(self):
        status, body = self.get_review_queue("?limit=1&offset=1")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 1)
        self.assertEqual(body["offset"], 1)
        self.assertEqual(len(body["jobs"]), 1)
        self.assertIsNotNone(body["jobs"][0]["edit"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["published"])
        self.assertEqual(self.get_job_state(1)["state"], "QC_PENDING_REVIEW")
        self.assertEqual(self.get_job_state(2)["state"], "QC_PENDING_REVIEW")

    def test_review_queue_handles_empty_queue(self):
        con = app.connect_db()
        app.execute(con, "DELETE FROM jobs")
        con.commit()
        con.close()
        status, body = self.get_review_queue("?limit=5&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(body["jobs"], [])
        self.assertEqual(body["total"], 0)
        self.assertIsNone(body["next_offset"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["published"])



class BoundedRetryPolicyTests(unittest.TestCase):
    def test_provider_403_is_not_retryable(self):
        exc = urllib.error.HTTPError("https://provider.invalid", 403, "forbidden", {}, None)
        self.assertFalse(app.is_retryable_render_failure(exc))

    def test_provider_400_is_not_retryable(self):
        exc = urllib.error.HTTPError("https://provider.invalid", 400, "bad request", {}, None)
        self.assertFalse(app.is_retryable_render_failure(exc))

    def test_rate_limit_and_server_errors_are_retryable(self):
        for code in (429, 500, 502, 599):
            with self.subTest(code=code):
                exc = urllib.error.HTTPError("https://provider.invalid", code, "temporary", {}, None)
                self.assertTrue(app.is_retryable_render_failure(exc))

    def test_network_error_is_retryable_but_unexpected_error_is_not(self):
        self.assertTrue(app.is_retryable_render_failure(urllib.error.URLError("temporary network failure")))
        self.assertFalse(app.is_retryable_render_failure(RuntimeError("invalid payload")))

    def test_retry_budget_is_bounded(self):
        self.assertTrue(app.should_retry_render_failure(True, 0))
        self.assertTrue(app.should_retry_render_failure(True, 1))
        self.assertTrue(app.should_retry_render_failure(True, 2))
        self.assertFalse(app.should_retry_render_failure(True, 3))
        self.assertFalse(app.should_retry_render_failure(False, 0))

    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual([app.render_retry_delay_seconds(n) for n in range(4)], [60, 120, 240, 480])
        self.assertEqual(app.render_retry_delay_seconds(8), 900)

    def test_invalid_retry_count_fails_closed(self):
        self.assertFalse(app.should_retry_render_failure(True, "not-a-number"))


if __name__ == "__main__":
    unittest.main()
