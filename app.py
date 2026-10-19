#!/usr/bin/env python3
"""IA10 renderer bridge with guarded queue. Provider/API secrets are environment-only."""
import json, mimetypes, os, sqlite3, subprocess, threading, time, traceback, urllib.error, urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "10000"))
MAX_BODY = 1_000_000
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("IA10_DB_PATH", "/tmp/ia10-queue.sqlite3")
MEDIA_DIR = Path(os.environ.get("IA10_MEDIA_DIR", "/tmp/ia10-media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def connect_db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        con = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5)
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, edit_json TEXT NOT NULL,
          state TEXT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
          provider_id TEXT, render_url TEXT, error TEXT)""")
    else:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, edit_json TEXT NOT NULL,
          state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          provider_id TEXT, render_url TEXT, error TEXT)""")
    # Add the render URL column to existing queue databases without losing jobs.
    if isinstance(con, sqlite3.Connection):
        try:
            con.execute("ALTER TABLE jobs ADD COLUMN render_url TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    else:
        con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS render_url TEXT")
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

def render_local_test_video(filename):
    """Create a clearly labelled diagnostic MP4; never used for production jobs."""
    from PIL import Image, ImageDraw, ImageFont
    import imageio_ffmpeg
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    image_path = MEDIA_DIR / (filename + ".png")
    video_path = MEDIA_DIR / filename
    im = Image.new("RGB", (1080, 1920), (12, 20, 32))
    draw = ImageDraw.Draw(im)
    # Use the no-argument bitmap font for compatibility across Pillow versions.
    font_big = ImageFont.load_default()
    font_mid = ImageFont.load_default()
    font_small = ImageFont.load_default()
    draw.rounded_rectangle((70, 280, 1010, 1640), radius=44, fill=(20, 35, 52), outline=(50, 190, 180), width=5)
    draw.text((115, 430), "IA 3.0", font=font_big, fill=(255, 255, 255))
    draw.text((115, 590), "PRUEBA TECNICA", font=font_mid, fill=(65, 220, 205))
    draw.line((115, 700, 900, 700), fill=(65, 220, 205), width=5)
    draw.text((115, 800), "Render local activo", font=font_mid, fill=(255, 255, 255))
    draw.text((115, 900), "Video de diagnostico", font=font_mid, fill=(210, 220, 232))
    draw.text((115, 1480), "NO APROBADO PARA PUBLICACION", font=font_small, fill=(255, 190, 90))
    im.save(image_path, format="PNG")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        subprocess.run([ffmpeg, "-y", "-loop", "1", "-i", str(image_path), "-t", "5",
                        "-r", "24", "-vf", "scale=1080:1920", "-c:v", "libx264",
                        "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(video_path)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace")[-500:]
        raise RuntimeError("ffmpeg_diagnostic_failed: " + detail)
    finally:
        try:
            image_path.unlink()
        except Exception:
            pass
    return video_path

def submit_render(edit):
    # Environment values may be pasted with surrounding whitespace/newlines.
    # Strip surrounding whitespace before constructing HTTP headers.
    key = os.environ.get("SHOTSTACK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("renderer_not_configured")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise RuntimeError("renderer_key_contains_control_characters")
    base = os.environ.get("SHOTSTACK_API_BASE", "https://api.shotstack.io/edit/stage").strip().rstrip("/")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in base):
        raise RuntimeError("renderer_base_url_contains_control_characters")
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
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/"):
            db_mode = "postgres" if DATABASE_URL else "sqlite-local-ephemeral"
            db_ok = False
            queue_counts = {}
            test_render = {"state": "unknown", "render_url_available": False, "error": None}
            try:
                con = connect_db()
                db_ok = True
                rows = execute(con, "SELECT state, COUNT(*) AS count FROM jobs GROUP BY state").fetchall()
                queue_counts = {str(row["state"]): int(row["count"]) for row in rows}
                row = execute(con, "SELECT state, render_url, error FROM jobs WHERE title LIKE %s ORDER BY id DESC LIMIT 1",
                              ("IA10_BOOTSTRAP_TEST_RENDER%",)).fetchone()
                if row:
                    test_render = {"state": str(row["state"]), "render_url_available": bool(row["render_url"]),
                                   "error": str(row["error"] or "")[:180] or None}
                con.close()
            except Exception as exc:
                test_render["error"] = "database_check_failed"
            self.send_json(200 if db_ok else 503, {
              "service":"ia10-renderer-bridge", "status":"alive" if db_ok else "degraded",
              "database_mode":db_mode, "database_ok":db_ok,
              "render_configured":bool(os.environ.get("SHOTSTACK_API_KEY")),
              "auth_configured":bool(os.environ.get("IA10_BRIDGE_TOKEN")),
              "bootstrap_test_enabled":os.environ.get("IA10_BOOTSTRAP_TEST_RENDER", "").strip().lower() in ("1", "true", "yes"),
              "queue_counts":queue_counts, "bootstrap_test_render":test_render,
              "note":"Health does not prove provider validity, video QC, or publication."
            })
            return
        if path.startswith("/media/") and self.command == "GET":
            filename = path[len("/media/"):]
            if not filename or Path(filename).name != filename or not filename.endswith(".mp4"):
                return self.send_json(404, {"error":"not_found"})
            media_path = MEDIA_DIR / filename
            try:
                data = media_path.read_bytes()
            except OSError:
                # This one fixed diagnostic artifact is reproducible on demand because
                # Render's ephemeral filesystem is not shared across deploy instances.
                # Regenerate only numbered diagnostic artifacts; never serve arbitrary files.
                is_diagnostic = (filename.startswith("ia10-test-") and
                                 filename.endswith(".mp4") and
                                 filename[len("ia10-test-"):-4].isdigit())
                if not is_diagnostic:
                    return self.send_json(404, {"error":"media_not_found"})
                try:
                    render_local_test_video(filename)
                    data = media_path.read_bytes()
                except Exception as exc:
                    print("diagnostic_media_regeneration_failed=%s" % str(exc)[:180], flush=True)
                    return self.send_json(503, {"error":"diagnostic_media_regeneration_failed"})
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/jobs":
            if not self.authorized():
                return self.send_json(401, {"error":"unauthorized"})
            try:
                con = connect_db()
                jobs = [dict(r) for r in execute(con,
                  "SELECT id,title,state,created_at,updated_at,provider_id,render_url,error FROM jobs ORDER BY id DESC LIMIT 100")]
                con.close()
                return self.send_json(200, {"jobs":jobs})
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})
        self.send_json(404, {"error":"not_found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self.authorized():
            return self.send_json(401, {"error":"unauthorized"})
        try:
            body = parse_body(self)
        except Exception:
            return self.send_json(400, {"error":"invalid_json_or_body_size"})

        if path == "/jobs":
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

        if path == "/jobs/process-next":
            try:
                con = connect_db()
                queued = execute(con, "SELECT * FROM jobs WHERE state='QUEUED' ORDER BY id LIMIT 5").fetchall()
                db_fingerprint = "postgres" if DATABASE_URL else "sqlite"
                if DATABASE_URL:
                    try:
                        import hashlib
                        db_fingerprint = hashlib.sha256(DATABASE_URL.encode("utf-8")).hexdigest()[:12]
                    except Exception:
                        db_fingerprint = "postgres"
                print("queue_probe db=%s queued_count_sample=%s first=%s" % (
                    db_fingerprint, len(queued), str(dict(queued[0]) if queued else None)[:240]), flush=True)
                job = queued[0] if queued else None
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
                # Keep provider diagnostics useful without logging request headers or API keys.
                provider_hint = ""
                try:
                    raw_error = exc.read(4096).decode("utf-8", "replace")
                    try:
                        error_json = json.loads(raw_error)
                        candidates = [error_json.get("message"), error_json.get("error"), error_json.get("status")]
                        response_obj = error_json.get("response")
                        if isinstance(response_obj, dict):
                            candidates.extend([response_obj.get("message"), response_obj.get("error")])
                        provider_hint = " ".join(str(v) for v in candidates if isinstance(v, (str, int)) and str(v).strip())
                    except Exception:
                        provider_hint = ""
                    provider_hint = "".join(ch for ch in provider_hint if ch.isalnum() or ch in " ._:-")[:160].strip()
                except Exception:
                    provider_hint = ""
                detail = "provider_http_%s" % exc.code
                if provider_hint:
                    detail += ":" + provider_hint
                # Only the bootstrap diagnostic may use a local fallback. Never substitute
                # a placeholder for a real content job or mark it publish-ready.
                if str(job["title"]).startswith("IA10_BOOTSTRAP_TEST_RENDER"):
                    try:
                        filename = "ia10-test-%s.mp4" % job["id"]
                        render_local_test_video(filename)
                        public_url = os.environ.get("IA10_PUBLIC_BASE_URL", "https://ia10-renderer-bridge.onrender.com").rstrip("/") + "/media/" + filename
                        con = connect_db()
                        execute(con, "UPDATE jobs SET state=%s,render_url=%s,error=%s,updated_at=%s WHERE id=%s",
                                ("FAILED", public_url, ("Shotstack Sandbox request rejected; local diagnostic MP4 is not a production render. " + detail)[:180], int(time.time()), job["id"]))
                        con.commit(); con.close()
                        return self.send_json(502, {"processed":True,"id":job["id"],"state":"FAILED",
                            "renderer":"local-test-fallback","diagnostic_url":public_url,
                            "error":"shotstack_sandbox_key_rejected",
                            "detail":"A diagnostic MP4 was generated, but the production render failed. This job is not approved for publication."})
                    except Exception as fallback_exc:
                        # Log a short diagnostic without exposing credentials or request headers.
                        print("diagnostic_fallback_error type=%s detail=%s" % (type(fallback_exc).__name__, str(fallback_exc)[:240]), flush=True)
                        detail += "; local_test_fallback_failed_" + type(fallback_exc).__name__
            except Exception as exc:
                detail = "render_error_" + type(exc).__name__
                # Traceback gives the failing code location; do not log locals, headers, or credentials.
                print("render_exception_traceback\\n" + traceback.format_exc()[-3000:], flush=True)
            try:
                con = connect_db()
                execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s",
                  (detail,int(time.time()),job["id"]))
                con.commit(); con.close()
            except Exception:
                pass
            return self.send_json(502, {"error":"render_failed","id":job["id"],"detail":detail})

        if path == "/render":
            edit = body.get("edit") if isinstance(body,dict) else None
            if not isinstance(edit,dict) or not isinstance(edit.get("timeline"),dict) or not isinstance(edit.get("output"),dict):
                return self.send_json(400, {"error":"expected_shotstack_edit_with_timeline_and_output"})
            try:
                result = submit_render(edit)
                return self.send_json(200, {"submitted":True,"provider_response":result})
            except urllib.error.HTTPError as exc:
                return self.send_json(502, {"error":"shotstack_rejected_request","provider_status":exc.code,"detail":"provider_rejected_request"})
            except Exception as exc:
                return self.send_json(503 if str(exc)=="renderer_not_configured" else 502, {"error":"render_failed","detail":"render_error_" + type(exc).__name__})
        self.send_json(404, {"error":"not_found"})

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/"):
            self.send_response(200); self.send_header("Cache-Control","no-store"); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        print("%s - %s"%(self.log_date_time_string(),fmt%args),flush=True)

def technical_video_qc(url):
    """Decode a provider MP4 and check basic technical properties; never approves editorial content."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False, "missing_or_non_https_render_url"
    try:
        import re, imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.run([ffmpeg, "-i", url, "-f", "null", "-"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        output = (proc.stderr or b"").decode("utf-8", "replace")
        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        duration = (int(duration_match.group(1)) * 3600 + int(duration_match.group(2)) * 60 +
                    float(duration_match.group(3))) if duration_match else 0
        dimensions = None
        for line in output.splitlines():
            if "Video:" in line:
                match = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if match:
                    dimensions = (int(match.group(1)), int(match.group(2)))
                    break
        if proc.returncode != 0:
            return False, "ffmpeg_decode_failed"
        if not dimensions:
            return False, "video_stream_or_dimensions_missing"
        width, height = dimensions
        if height <= width:
            return False, "not_portrait"
        if not 4 <= duration <= 15:
            return False, "duration_out_of_range"
        return True, "decoded_portrait_%sx%s_duration_%.2fs" % (width, height, duration)
    except subprocess.TimeoutExpired:
        return False, "technical_qc_timeout"
    except Exception as exc:
        return False, "technical_qc_error_" + type(exc).__name__

def poll_submitted_render():
    """Poll one submitted Shotstack render and persist its terminal state."""
    key = os.environ.get("SHOTSTACK_API_KEY", "").strip()
    if not key:
        return
    con = connect_db()
    try:
        job = execute(con, "SELECT id,provider_id,state,render_url,title FROM jobs WHERE (state='RENDER_SUBMITTED' AND provider_id IS NOT NULL AND provider_id<>'') OR (state='RENDERED' AND title LIKE 'IA10_BOOTSTRAP_TEST_RENDER%' AND render_url IS NOT NULL AND render_url<>'') ORDER BY updated_at LIMIT 1").fetchone()
    finally:
        con.close()
    if not job:
        return
    if str(job.get("state", "")) == "RENDERED":
        passed, detail = technical_video_qc(str(job.get("render_url") or ""))
        new_state = "QC_PENDING_REVIEW" if passed else "QC_FAILED"
        con = connect_db()
        execute(con, "UPDATE jobs SET state=%s,error=%s,updated_at=%s WHERE id=%s AND state='RENDERED'",
                (new_state, detail[:180], int(time.time()), job["id"]))
        con.commit(); con.close()
        print("technical_qc id=%s state=%s result=%s" % (job["id"], new_state, detail[:120]), flush=True)
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
            render_url = data.get("url") or data.get("output") or ""
            passed, detail = technical_video_qc(str(render_url))
            new_state = "QC_PENDING_REVIEW" if passed else "QC_FAILED"
            con = connect_db()
            execute(con, "UPDATE jobs SET state=%s,render_url=%s,error=%s,updated_at=%s WHERE id=%s AND state='RENDER_SUBMITTED'",
                    (new_state, str(render_url)[:2000], detail[:180], now, job["id"]))
            con.commit(); con.close()
            print("render_poll id=%s state=%s result=%s" % (job["id"], new_state, detail[:120]), flush=True)
        elif status in ("failed", "error"):
            detail = "shotstack_render_failed"
            con = connect_db()
            execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s AND state='RENDER_SUBMITTED'",
                    (detail, now, job["id"]))
            con.commit(); con.close()
            print("render_poll id=%s state=FAILED" % job["id"], flush=True)
        else:
            print("render_poll id=%s provider_status=%s" % (job["id"], status or "unknown"), flush=True)
    except Exception as exc:
        print("render_poll_error id=%s type=%s" % (job["id"], type(exc).__name__), flush=True)

def recover_interrupted_bootstrap_jobs():
    """Recover only diagnostic jobs interrupted before a provider render ID was assigned."""
    con = connect_db()
    try:
        cur = execute(con, "UPDATE jobs SET state=%s,error=%s,updated_at=%s WHERE state='PROCESSING' AND title LIKE %s AND (provider_id IS NULL OR provider_id='')",
                      ("QUEUED", "requeued_after_interrupted_diagnostic", int(time.time()), "IA10_BOOTSTRAP_TEST_RENDER%"))
        count = cur.rowcount
        con.commit()
        print("queue_recovery requeued_bootstrap=%s" % count, flush=True)
    finally:
        con.close()

def bootstrap_test_render_once():
    """Optionally enqueue one idempotent sandbox render without exposing a public unauthenticated endpoint."""
    if os.environ.get("IA10_BOOTSTRAP_TEST_RENDER", "").strip().lower() not in ("1", "true", "yes"):
        return
    # Optional run ID allows an explicitly requested fresh diagnostic without duplicating services.
    run_id = os.environ.get("IA10_BOOTSTRAP_TEST_RUN_ID", "").strip()
    if run_id and not all(ch.isalnum() or ch in "-_" for ch in run_id):
        print("bootstrap_test_render invalid_run_id", flush=True)
        return
    title = "IA10_BOOTSTRAP_TEST_RENDER_1080_VERTICAL_5" + ("_" + run_id if run_id else "")
    edit = {
        "timeline": {
            "background": "#101820",
            "tracks": [{
                "clips": [{
                    "asset": {
                        "type": "image",
                        "src": "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1080&q=80"
                    },
                    "start": 0,
                    "length": 5,
                    "fit": "crop"
                }]
            }]
        },
        "output": {
            "format": "mp4",
            "resolution": "1080",
            "aspectRatio": "9:16"
        }
    }
    con = connect_db()
    try:
        existing = execute(con, "SELECT id,state,error FROM jobs WHERE title=%s LIMIT 1", (title,)).fetchone()
        if existing:
            # Requeue only this named diagnostic bootstrap job after known queue
            # or provider-auth failures, so a deploy can verify the current provider key.
            existing_error = str(existing.get("error") or "")
            if existing["state"] == "FAILED" and (
                "render_error_KeyError" in existing_error or
                "shotstack_sandbox_key_rejected" in existing_error or
                "Shotstack Sandbox request rejected" in existing_error
            ):
                now = int(time.time())
                execute(con, "UPDATE jobs SET state='QUEUED',error=NULL,render_url=NULL,provider_id=NULL,updated_at=%s WHERE id=%s",
                        (now, existing["id"]))
                con.commit()
                print("bootstrap_test_render requeued_diagnostic_retry id=%s" % existing["id"], flush=True)
            else:
                print("bootstrap_test_render already_exists id=%s state=%s" % (existing["id"], existing["state"]), flush=True)
            return
        now = int(time.time())
        inserted = execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (title, json.dumps(edit), "QUEUED", now, now)).fetchone()
        con.commit()
        db_fingerprint = "postgres" if DATABASE_URL else "sqlite"
        if DATABASE_URL:
            try:
                import hashlib
                db_fingerprint = hashlib.sha256(DATABASE_URL.encode("utf-8")).hexdigest()[:12]
            except Exception:
                db_fingerprint = "postgres"
        print("bootstrap_test_render queued_once id=%s db=%s" % (inserted["id"], db_fingerprint), flush=True)
    finally:
        con.close()

def background_queue_worker():
    """Poll the guarded queue while this free web service is awake."""
    interval = max(30, int(os.environ.get("IA10_WORKER_INTERVAL_SECONDS", "60")))
    print("queue_worker_started interval=%s provider_key_configured=%s auth_token_configured=%s" % (
        interval, bool(os.environ.get("SHOTSTACK_API_KEY", "").strip()),
        bool(os.environ.get("IA10_BRIDGE_TOKEN", ""))), flush=True)
    while True:
        time.sleep(interval)
        # Never consume queued jobs until a provider key is configured.
        if not os.environ.get("SHOTSTACK_API_KEY", "").strip():
            continue
        try:
            poll_submitted_render()
        except Exception as exc:
            print("render_poll_worker_error type=%s" % type(exc).__name__, flush=True)
        token = os.environ.get("IA10_BRIDGE_TOKEN", "")
        if not token:
            continue
        req = urllib.request.Request(
            "http://127.0.0.1:%s/jobs/process-next" % PORT,
            data=b"{}",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=135) as response:
                payload = response.read(1000).decode("utf-8", "replace")
                print("queue_worker status=%s result=%s" % (response.status, payload), flush=True)
        except urllib.error.HTTPError as exc:
            # Log only safe response fields; never log request headers or credentials.
            try:
                raw = exc.read(4096).decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {}
                safe = {k: payload.get(k) for k in ("error", "state", "renderer", "diagnostic_url", "detail") if isinstance(payload, dict) and isinstance(payload.get(k), (str, int, bool))}
                print("queue_worker_http_error status=%s response=%s" % (exc.code, json.dumps(safe, ensure_ascii=True)[:1200]), flush=True)
            except Exception:
                print("queue_worker_http_error status=%s response_parse_failed" % exc.code, flush=True)
        except Exception as exc:
            print("queue_worker_error type=%s" % type(exc).__name__, flush=True)

connect_db().close()
recover_interrupted_bootstrap_jobs()
bootstrap_test_render_once()
threading.Thread(target=background_queue_worker, name="ia10-queue-worker", daemon=True).start()
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()