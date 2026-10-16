#!/usr/bin/env python3
"""IA10 renderer bridge with guarded queue. Provider/API secrets are environment-only."""
import json, os, sqlite3, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "10000"))
MAX_BODY = 1_000_000
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("IA10_DB_PATH", "/tmp/ia10-queue.sqlite3")

def connect_db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        con = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5)
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, edit_json TEXT NOT NULL,
          state TEXT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
          provider_id TEXT, error TEXT)""")
    else:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, edit_json TEXT NOT NULL,
          state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          provider_id TEXT, error TEXT)""")
    con.commit()
    return con

def execute(con, sql, params=()):
    if isinstance(con, sqlite3.Connection):
        sql = sql.replace("%s", "?")
    return con.execute(sql, params)

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
            db_mode = "postgres" if DATABASE_URL else "sqlite-local-ephemeral"
            try:
                con = connect_db()
                con.close()
                db_ok = True
            except Exception:
                db_ok = False
            self.send_json(200 if db_ok else 503, {
              "service":"ia10-renderer-bridge", "status":"alive" if db_ok else "degraded",
              "database_mode":db_mode, "database_ok":db_ok,
              "render_configured":bool(os.environ.get("SHOTSTACK_API_KEY")),
              "auth_configured":bool(os.environ.get("IA10_BRIDGE_TOKEN")),
              "note":"Health does not prove provider validity, video QC, or publication."
            })
            return
        if self.path == "/jobs":
            if not self.authorized():
                return self.send_json(401, {"error":"unauthorized"})
            try:
                con = connect_db()
                jobs = [dict(r) for r in execute(con,
                  "SELECT id,title,state,created_at,updated_at,provider_id,error FROM jobs ORDER BY id DESC LIMIT 100")]
                con.close()
                return self.send_json(200, {"jobs":jobs})
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})
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
            try:
                con = connect_db()
                row = execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                  (title.strip()[:200], json.dumps(edit), "QUEUED", now, now)).fetchone()
                jid = row["id"]
                con.commit(); con.close()
                return self.send_json(201, {"id":jid,"state":"QUEUED","note":"Queued; not yet rendered or published."})
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})

        if self.path == "/jobs/process-next":
            try:
                con = connect_db()
                job = execute(con, "SELECT * FROM jobs WHERE state='QUEUED' ORDER BY id LIMIT 1").fetchone()
                if not job:
                    con.close()
                    return self.send_json(200, {"processed":False,"note":"queue_empty"})
                execute(con, "UPDATE jobs SET state='PROCESSING',updated_at=%s WHERE id=%s", (int(time.time()),job["id"]))
                con.commit(); con.close()
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})
            try:
                result = submit_render(json.loads(job["edit_json"]))
                provider_id = (result.get("response") or {}).get("id") or (result.get("data") or {}).get("id") or ""
                con = connect_db()
                execute(con, "UPDATE jobs SET state='RENDER_SUBMITTED',provider_id=%s,updated_at=%s WHERE id=%s",
                  (str(provider_id),int(time.time()),job["id"]))
                con.commit(); con.close()
                return self.send_json(200, {"processed":True,"id":job["id"],"state":"RENDER_SUBMITTED","provider_response":result})
            except urllib.error.HTTPError as exc:
                detail = "provider_http_%s: %s"%(exc.code,exc.read(1500).decode("utf-8","replace"))
            except Exception as exc:
                detail = str(exc)[:300]
            try:
                con = connect_db()
                execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s",
                  (detail,int(time.time()),job["id"]))
                con.commit(); con.close()
            except Exception:
                pass
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

def poll_submitted_render():
    """Poll one submitted Shotstack render and persist its terminal state."""
    key = os.environ.get("SHOTSTACK_API_KEY", "").strip()
    if not key:
        return
    con = connect_db()
    try:
        job = execute(con, "SELECT id,provider_id FROM jobs WHERE state='RENDER_SUBMITTED' AND provider_id IS NOT NULL AND provider_id<>'' ORDER BY id LIMIT 1").fetchone()
    finally:
        con.close()
    if not job:
        return
    base = os.environ.get("SHOTSTACK_API_BASE", "https://api.shotstack.io/edit/stage").rstrip("/")
    req = urllib.request.Request(base + "/render/" + str(job["provider_id"]),
        headers={"x-api-key": key}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("response") or payload.get("data") or {}
        status = str(data.get("status", "")).lower()
        now = int(time.time())
        if status in ("done", "completed", "complete"):
            con = connect_db()
            execute(con, "UPDATE jobs SET state='RENDERED',updated_at=%s WHERE id=%s AND state='RENDER_SUBMITTED'",
                    (now, job["id"]))
            con.commit(); con.close()
            print("render_poll id=%s state=RENDERED" % job["id"], flush=True)
        elif status in ("failed", "error"):
            detail = str(data.get("error") or data.get("message") or "shotstack_render_failed")[:300]
            con = connect_db()
            execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s AND state='RENDER_SUBMITTED'",
                    (detail, now, job["id"]))
            con.commit(); con.close()
            print("render_poll id=%s state=FAILED" % job["id"], flush=True)
        else:
            print("render_poll id=%s provider_status=%s" % (job["id"], status or "unknown"), flush=True)
    except Exception as exc:
        print("render_poll_error id=%s error=%s" % (job["id"], str(exc)[:180]), flush=True)

def background_queue_worker():
    """Poll the guarded queue while this free web service is awake."""
    interval = max(30, int(os.environ.get("IA10_WORKER_INTERVAL_SECONDS", "60")))
    while True:
        time.sleep(interval)
        # Never consume queued jobs until a provider key is configured.
        if not os.environ.get("SHOTSTACK_API_KEY", "").strip():
            continue
        poll_submitted_render()
        token = os.environ.get("IA10_BRIDGE_TOKEN", "")
        if not token:
            continue
        req = urllib.request.Request(
            "http://127.0.0.1:%s/jobs/process-next" % PORT,
            data=b"{}",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = response.read(1000).decode("utf-8", "replace")
                print("queue_worker status=%s result=%s" % (response.status, payload), flush=True)
        except Exception as exc:
            print("queue_worker_error=%s" % str(exc)[:200], flush=True)

connect_db().close()
threading.Thread(target=background_queue_worker, name="ia10-queue-worker", daemon=True).start()
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
