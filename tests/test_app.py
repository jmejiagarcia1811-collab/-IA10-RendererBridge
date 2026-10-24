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


if __name__ == "__main__":
    unittest.main()
