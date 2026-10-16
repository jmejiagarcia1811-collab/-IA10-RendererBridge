#!/usr/bin/env python3
"""Minimal guarded IA10 Shotstack bridge. No secrets are stored in source control."""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "10000"))
MAX_BODY = 1_000_000

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/health", "/"):
            self.send_json(200, {
                "service": "ia10-renderer-bridge",
                "status": "alive",
                "render_configured": bool(os.environ.get("SHOTSTACK_API_KEY")),
                "auth_configured": bool(os.environ.get("IA10_BRIDGE_TOKEN")),
                "note": "Alive does not mean rendering is ready."
            })
        else:
            self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/render":
            self.send_json(404, {"error": "not_found"})
            return
        token = os.environ.get("IA10_BRIDGE_TOKEN", "")
        key = os.environ.get("SHOTSTACK_API_KEY", "")
        if not token or not key:
            self.send_json(503, {"error": "renderer_not_configured"})
            return
        if self.headers.get("Authorization", "") != "Bearer " + token:
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > MAX_BODY:
                self.send_json(413, {"error": "invalid_body_size"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            edit = payload.get("edit") if isinstance(payload, dict) else None
            if not isinstance(edit, dict) or not isinstance(edit.get("timeline"), dict) or not isinstance(edit.get("output"), dict):
                self.send_json(400, {"error": "expected_shotstack_edit_with_timeline_and_output"})
                return
            base = os.environ.get("SHOTSTACK_API_BASE", "https://api.shotstack.io/edit/stage").rstrip("/")
            request = urllib.request.Request(
                base + "/render",
                data=json.dumps({"timeline": edit["timeline"], "output": edit["output"]}).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-api-key": key},
                method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    self.send_json(response.status, {"submitted": True, "provider_response": result})
            except urllib.error.HTTPError as exc:
                body = exc.read(2000).decode("utf-8", "replace")
                self.send_json(502, {"error": "shotstack_rejected_request", "provider_status": exc.code, "detail": body})
            except (urllib.error.URLError, TimeoutError) as exc:
                self.send_json(502, {"error": "shotstack_unreachable", "detail": str(exc)[:300]})
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid_json"})
        except Exception:
            self.send_json(500, {"error": "internal_error"})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt % args), flush=True)

if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
