#!/usr/bin/env python3
"""IA10 renderer bridge + guarded job queue. Secrets stay in environment variables."""
import json, os, sqlite3, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "10000"))
MAX_BODY = 1_000_000
DB_PATH = os.environ.get("IA10_DB_PATH", "/tmp/ia10-queue.sqlite3")

def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, edit_json TEXT NOT NULL,
      state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      provider_id TEXT, error TEXT)""")
    con.commit()
    return con

def parse_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 2 or length > MAX_BODY:
        raise ValueError("invalid_body_size")
    return json.loads(handler.rfile.read(length).decode("utf-8"))

def submit_render(edit):
    key = os.environ.get("SHOTSTACK_API_KEY", "")
    if not key:
        raise RuntimeError("renderer_not_configured")
    base = os.environ.get("SHOTSTACK_API_BASE", "https://api.shotstack.io/edit/stage").rstrip("/")
    req = urllib.request.Request(base + "/render",
      data=json.dumps({"timeline": edit["timeline"], "output": edit["output"]}).encode(),
      headers={"Content-Type":"application/json","x-api-key":key}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        token = os.environ.get("IA10_BRIDGE_TOKEN", "")
        return bool(token) and self.headers.get("Authorization", "") == "Bearer " + token

    def do_GET(self):
        if self.path in ("/health", "/"):
            self.send_json(200, {
              "service":"ia10-renderer-bridge", "status":"alive",
              "render_configured":bool(os.environ.get("SHOTSTACK_API_KEY")),
              "auth_configured":bool(os.environ.get("IA10_BRIDGE_TOKEN")),
              "queue":"sqlite-local-ephemeral",
              "note":"Health does not prove provider validity, video QC, or publication."
            })
            return
        if self.path == "/jobs":
            if not self.authorized():
                return self.send_json(401, {"error":"unauthorized"})
            con = connect_db()
            jobs = [dict(r) for r in con.execute(
              "SELECT id,title,state,created_at,updated_at,provider_id,error FROM jobs ORDER BY id DESC LIMIT 100")]
            con.close()
            return self.send_json(200, {"jobs":jobs})
        self.send_json(404, {"error":"not_found"})

    def do_POST(self):
        if not self.authorized():
            return self.send_json(401, {"error":"unauthorized"})
        try:
            body = parse_body(self)
        except Exception:
            return self.send_json(400, {"error":"invalid_json_or_body_size"})

        if self.path == "/jobs":
            title, edit = body.get("title"), body.get("edit")
            if not isinstance(title, str) or not title.strip() or not isinstance(edit, dict) or not isinstance(edit.get("timeline"), dict) or not isinstance(edit.get("output"), dict):
                return self.send_json(400, {"error":"title_and_shotstack_edit_required"})
            now = int(time.time())
            con = connect_db()
            cur = con.execute("INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(?,?,?,?,?)",
              (title.strip()[:200], json.dumps(edit), "QUEUED", now, now))
            jid = cur.lastrowid
            con.commit(); con.close()
            return self.send_json(201, {"id":jid,"state":"QUEUED","note":"Queued; not yet rendered or published."})

        if self.path == "/jobs/process-next":
            con = connect_db()
            job = con.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY id LIMIT 1").fetchone()
            if not job:
                con.close()
                return self.send_json(200, {"processed":False,"note":"queue_empty"})
            now = int(time.time())
            con.execute("UPDATE jobs SET state='PROCESSING',updated_at=? WHERE id=?", (now,job["id"]))
            con.commit(); con.close()
            try:
                result = submit_render(json.loads(job["edit_json"]))
                provider_id = (result.get("response") or {}).get("id") or (result.get("data") or {}).get("id") or ""
                con = connect_db()
                con.execute("UPDATE jobs SET state='RENDER_SUBMITTED',provider_id=?,updated_at=? WHERE id=?",
                  (str(provider_id),int(time.time()),job["id"]))
                con.commit(); con.close()
                return self.send_json(200, {"processed":True,"id":job["id"],"state":"RENDER_SUBMITTED","provider_response":result})
            except urllib.error.HTTPError as exc:
                detail = exc.read(1500).decode("utf-8","replace")
                state, detail = "FAILED", "provider_http_%s: %s"%(exc.code,detail)
            except Exception as exc:
                state, detail = "FAILED", str(exc)[:300]
            con = connect_db()
            con.execute("UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?",
              (state,detail,int(time.time()),job["id"]))
            con.commit(); con.close()
            return self.send_json(502, {"error":"render_failed","id":job["id"],"detail":detail})

        if self.path == "/render":
            edit = body.get("edit") if isinstance(body,dict) else None
            if not isinstance(edit,dict) or not isinstance(edit.get("timeline"),dict) or not isinstance(edit.get("output"),dict):
                return self.send_json(400, {"error":"expected_shotstack_edit_with_timeline_and_output"})
            try:
                result = submit_render(edit)
                return self.send_json(200, {"submitted":True,"provider_response":result})
            except urllib.error.HTTPError as exc:
                return self.send_json(502, {"error":"shotstack_rejected_request","provider_status":exc.code,"detail":exc.read(1500).decode("utf-8","replace")})
            except Exception as exc:
                return self.send_json(503 if str(exc)=="renderer_not_configured" else 502, {"error":"render_failed","detail":str(exc)[:300]})
        self.send_json(404, {"error":"not_found"})

    def do_HEAD(self):
        if self.path in ("/health", "/"):
            self.send_response(200); self.send_header("Cache-Control","no-store"); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        print("%s - %s"%(self.log_date_time_string(),fmt%args),flush=True)

connect_db().close()
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
