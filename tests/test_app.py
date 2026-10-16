import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
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

if __name__ == "__main__":
    unittest.main()
