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

class BridgeHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_review_queue_cors_preflight_allows_authorization_only_for_control_center(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={
                "Origin": "https://ia30-centro-control.onrender.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://ia30-centro-control.onrender.com")
            self.assertIn("Authorization", response.headers.get("Access-Control-Allow-Headers", ""))
            self.assertEqual(response.headers.get("Access-Control-Allow-Methods"), "GET, OPTIONS")

    def test_review_queue_cors_does_not_allow_untrusted_origin(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_control_center_cors_preflight_allows_authorization_header(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={
                "Origin": "https://ia30-centro-control.onrender.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://ia30-centro-control.onrender.com")
            self.assertIn("Authorization", response.headers.get("Access-Control-Allow-Headers", ""))
            self.assertEqual(response.headers.get("Access-Control-Allow-Methods"), "GET, OPTIONS")

    def test_untrusted_origin_does_not_receive_cors_permission(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_health_does_not_require_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            with urllib.request.urlopen(self.base + "/health", timeout=3) as response:
                body = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(body["status"], "alive")
                self.assertFalse(body["render_configured"])
                self.assertFalse(body["auth_configured"])

    def test_render_is_blocked_without_credentials(self):
        request = urllib.request.Request(self.base + "/render", data=b'{"edit":{"timeline":{},"output":{}}}', headers={"Content-Type":"application/json"}, method="POST")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 503)

    def test_render_rejects_missing_bearer_token(self):
        request = urllib.request.Request(self.base + "/render", data=b'{"edit":{"timeline":{},"output":{}}}', headers={"Content-Type":"application/json"}, method="POST")
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY":"test-only-key","IA10_BRIDGE_TOKEN":"test-only-token"}, clear=True):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 401)


    def test_corrupt_diagnostic_media_is_regenerated_before_serving(self):
        valid_payload = b"\\x00\\x00\\x00\\x18ftypisom" + b"\\x00" * 2048
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / "ia10-test-1.mp4").write_bytes(b"broken-media-payload")

            def regenerate(filename):
                (media_dir / filename).write_bytes(valid_payload)

            with patch.object(app, "MEDIA_DIR", media_dir), patch.object(
                app, "render_local_test_video", side_effect=regenerate
            ):
                with urllib.request.urlopen(self.base + "/media/ia10-test-1.mp4", timeout=3) as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get("Content-Type"), "video/mp4")
                    self.assertEqual(int(response.headers.get("Content-Length")), len(valid_payload))
            self.assertEqual(body, valid_payload)

    def test_corrupt_local_media_is_regenerated_only_for_valid_job_record(self):
        valid_payload = b"\\x00\\x00\\x00\\x18ftypisom" + b"\\x00" * 2048
        job = {
            "id": 23,
            "title": "Título de prueba",
            "state": "QC_PENDING_REVIEW",
            "provider_id": "local-ffmpeg-edge-tts",
            "render_url": "https://ia10-renderer-bridge.onrender.com/media/local-23.mp4",
            "edit_json": json.dumps({
                "local_render": {
                    "headline": "Titular de prueba",
                    "script": "Este es un guion suficientemente largo para probar la recuperación segura del vídeo local.",
                    "source": "Fuente de prueba",
                }
            }),
        }

        class FakeConnection:
            def close(self):
                pass

        class FakeResult:
            def fetchone(self):
                return job

        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / "local-23.mp4").write_bytes(b"broken-media-payload")

            def regenerate(title, spec, filename, target_dir):
                Path(target_dir, filename).write_bytes(valid_payload)

            with patch.object(app, "MEDIA_DIR", media_dir), patch.object(
                app, "connect_db", return_value=FakeConnection()
            ), patch.object(app, "execute", return_value=FakeResult()), patch.object(
                app, "render_local_content", side_effect=regenerate
            ), patch.dict(os.environ, {
                "IA10_PUBLIC_BASE_URL": "https://ia10-renderer-bridge.onrender.com"
            }):
                with urllib.request.urlopen(self.base + "/media/local-23.mp4", timeout=3) as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get("Content-Type"), "video/mp4")
                    self.assertEqual(int(response.headers.get("Content-Length")), len(valid_payload))
            self.assertEqual(body, valid_payload)


    def test_review_queue_requires_bearer_token(self):
        with patch.dict(os.environ, {"IA10_REVIEW_TOKEN":"test-only-token"}, clear=True), patch.object(app, "connect_db") as connect:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + "/jobs/review-queue", timeout=3)
        self.assertEqual(caught.exception.code, 401)
        connect.assert_not_called()

    def test_review_queue_rejects_out_of_range_pagination(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=11&offset=0",
            headers={"Authorization":"Bearer test-only-token"},
        )
        with patch.dict(os.environ, {"IA10_REVIEW_TOKEN":"test-only-token"}, clear=True), patch.object(app, "connect_db") as connect:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        connect.assert_not_called()

    def test_review_queue_returns_empty_queue_without_mutation(self):
        class FakeConnection:
            def close(self):
                pass

        class FakeResult:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []
            def fetchone(self):
                return self.row
            def fetchall(self):
                return self.rows

        def fake_execute(_con, sql, params=None):
            if "COUNT(*)" in sql:
                return FakeResult(row={"count":0})
            return FakeResult(rows=[])

        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={"Authorization":"Bearer test-only-token"},
        )
        with patch.dict(os.environ, {"IA10_REVIEW_TOKEN":"test-only-token"}, clear=True), patch.object(
            app, "connect_db", return_value=FakeConnection()
        ), patch.object(app, "execute", side_effect=fake_execute):
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.loads(response.read())
                self.assertEqual(response.status, 200)
        self.assertEqual(body["jobs"], [])
        self.assertEqual(body["total"], 0)
        self.assertIsNone(body["next_offset"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["published"])

    def test_review_queue_paginates_pending_editorial_jobs(self):
        class FakeConnection:
            def close(self):
                pass

        class FakeResult:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []
            def fetchone(self):
                return self.row
            def fetchall(self):
                return self.rows

        job = {
            "id": 42, "title":"Vídeo pendiente", "state":"QC_PENDING_REVIEW",
            "created_at":"2026-10-25", "updated_at":"2026-10-25",
            "provider_id":"local-test", "render_url":"https://example.invalid/video.mp4",
            "error":None, "edit_json":json.dumps({"headline":"Titular de prueba"}),
        }
        def fake_execute(_con, sql, params=None):
            if "COUNT(*)" in sql:
                return FakeResult(row={"count":3})
            self.assertEqual(params, (2, 2))
            return FakeResult(rows=[job])

        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=2&offset=2",
            headers={"Authorization":"Bearer test-only-token"},
        )
        with patch.dict(os.environ, {"IA10_REVIEW_TOKEN":"test-only-token"}, clear=True), patch.object(
            app, "connect_db", return_value=FakeConnection()
        ), patch.object(app, "execute", side_effect=fake_execute):
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.loads(response.read())
                self.assertEqual(response.status, 200)
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["offset"], 2)
        self.assertIsNone(body["next_offset"])
        self.assertEqual(body["jobs"][0]["id"], 42)
        self.assertEqual(body["jobs"][0]["edit"], {"headline":"Titular de prueba"})
        self.assertTrue(body["read_only"])
        self.assertFalse(body["published"])

    def test_review_queue_rejects_general_bridge_token(self):
        request = urllib.request.Request(
            self.base + "/jobs/review-queue?limit=5&offset=0",
            headers={"Authorization":"Bearer bridge-token"},
        )
        with patch.dict(os.environ, {"IA10_BRIDGE_TOKEN":"bridge-token"}, clear=True), patch.object(app, "connect_db") as connect:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 401)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
