#!/usr/bin/env python3
"""IA10 renderer bridge with guarded queue. Provider/API secrets are environment-only."""
import json, mimetypes, os, shutil, sqlite3, subprocess, tempfile, threading, time, traceback, urllib.error, urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from local_renderer import render_local_content
from rss_ingest import ingest_rss_once

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
          provider_id TEXT, render_url TEXT, error TEXT, qc_review_json TEXT,
          retry_count INTEGER NOT NULL DEFAULT 0, retry_after BIGINT NOT NULL DEFAULT 0)""")
    else:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, edit_json TEXT NOT NULL,
          state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          provider_id TEXT, render_url TEXT, error TEXT, qc_review_json TEXT)""")
    # Add optional columns to existing queue databases without losing jobs.
    if isinstance(con, sqlite3.Connection):
        for column_definition in ("render_url TEXT", "qc_review_json TEXT", "retry_count INTEGER NOT NULL DEFAULT 0", "retry_after BIGINT NOT NULL DEFAULT 0"):
            try:
                con.execute("ALTER TABLE jobs ADD COLUMN " + column_definition)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    else:
        con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS render_url TEXT")
        con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS qc_review_json TEXT")
        con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0")
        con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_after BIGINT NOT NULL DEFAULT 0")
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

EDITORIAL_QC_CHECKS = (
    "editorial_reviewed",
    "facts_sources_verified",
    "spelling_names_dates_checked",
    "safe_margins_layout_checked",
    "audio_sync_checked",
    "duration_format_checked",
    "no_watermark_overlap",
)

def validate_editorial_qc(decision, checklist):
    """Validate an explicit checklist; this never publishes content."""
    if decision not in ("approve", "reject") or not isinstance(checklist, dict):
        return False
    if decision == "reject":
        return True
    return all(checklist.get(key) is True for key in EDITORIAL_QC_CHECKS)

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

def check_shotstack_credentials():
    """Read-only credential check against both Shotstack environments; never logs keys or bodies."""
    key = os.environ.get("SHOTSTACK_API_KEY", "").strip()
    if not key:
        print("provider_auth_check result=missing_key", flush=True)
        return
    configured = os.environ.get("SHOTSTACK_API_BASE", "https://api.shotstack.io/edit/stage").strip().rstrip("/")
    bases = []
    for candidate in (configured, "https://api.shotstack.io/edit/stage", "https://api.shotstack.io/edit/v1"):
        if candidate and candidate not in bases:
            bases.append(candidate)
    for base in bases:
        env_name = "stage" if base.endswith("/stage") else "production" if base.endswith("/v1") else "custom"
        try:
            req = urllib.request.Request(base + "/templates", headers={"Accept": "application/json", "x-api-key": key}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as response:
                print("provider_auth_probe environment=%s status=%s result=%s" % (env_name, response.status, "ok" if response.status == 200 else "unexpected_status"), flush=True)
        except urllib.error.HTTPError as exc:
            hint = "unauthorized" if exc.code == 401 else "forbidden_key_or_account_permissions" if exc.code == 403 else "http_error"
            print("provider_auth_probe environment=%s status=%s result=%s" % (env_name, exc.code, hint), flush=True)
        except Exception as exc:
            print("provider_auth_probe environment=%s result=network_error type=%s" % (env_name, type(exc).__name__), flush=True)


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
            diagnostics_ok = False
            queue_counts = {}
            failure_categories = {}
            test_render = {"state": "unknown", "render_url_available": False, "error": None}
            con = None
            diagnostic_stage = "database_connect"
            try:
                con = connect_db()
                db_ok = True
                diagnostic_stage = "queue_counts"
                rows = execute(con, "SELECT state, COUNT(*) AS count FROM jobs GROUP BY state").fetchall()
                queue_counts = {str(row["state"]): int(row["count"]) for row in rows}
                # Public diagnostics expose coarse categories only, never job titles,
                # provider IDs, render URLs, raw errors, or credentials.
                diagnostic_stage = "failure_categories"
                failure_rows = execute(con, """SELECT
                    CASE
                      WHEN POSITION('provider_http_403' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_http_403_forbidden'
                      WHEN POSITION('sandbox key rejected' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('shotstack_sandbox_key_rejected' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_sandbox_key_rejected'
                      WHEN POSITION('provider_response_missing_render_id' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_response_invalid'
                      WHEN POSITION('render_error_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'internal_render_error'
                      WHEN POSITION('provider_http_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('shotstack_render_failed' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_render_error'
                      WHEN POSITION('audio_stream_missing' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_audio_missing'
                      WHEN POSITION('render_url_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('download_failed' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_render_url'
                      WHEN POSITION('decode' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('invalid_or_incomplete' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_decode'
                      WHEN POSITION('duration_out_of_range' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('not_portrait' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('dimensions_missing' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_format'
                      WHEN COALESCE(to_jsonb(j)->>'error','') = '' THEN 'no_error_detail'
                      ELSE 'other'
                    END AS category, COUNT(*) AS count
                    FROM jobs j WHERE to_jsonb(j)->>'state' IN ('FAILED','QC_FAILED')
                    GROUP BY 1 ORDER BY 1""").fetchall()
                failure_categories = {str(row["category"]): int(row["count"]) for row in failure_rows}
                diagnostic_stage = "bootstrap_status"
                row = execute(con, """SELECT to_jsonb(j)->>'state' AS state,
                                      to_jsonb(j)->>'render_url' AS render_url,
                                      to_jsonb(j)->>'error' AS error
                                      FROM jobs j WHERE COALESCE(to_jsonb(j)->>'title','') LIKE %s
                                      ORDER BY (to_jsonb(j)->>'id')::bigint DESC LIMIT 1""",
                              ("IA10_BOOTSTRAP_TEST_RENDER%",)).fetchone()
                if row:
                    test_render = {"state": str(row["state"]), "render_url_available": bool(row["render_url"]),
                                   "error": str(row["error"] or "")[:180] or None}
                diagnostics_ok = True
            except Exception as exc:
                # Keep connectivity and diagnostic-query health distinct.
                if db_ok:
                    test_render = {"state": "unknown", "render_url_available": False,
                                   "error": "diagnostic_query_failed"}
                else:
                    test_render["error"] = "database_connection_failed"
                safe_db_message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc).splitlines()[0][:180]
                print("health_diagnostics_failed stage=%s type=%s detail=%s" % (diagnostic_stage, type(exc).__name__, safe_db_message.replace("\\n", " ")), flush=True)
            finally:
                if con is not None:
                    try:
                        con.close()
                    except Exception:
                        pass
            self.send_json(200 if db_ok else 503, {
              "service":"ia10-renderer-bridge", "status":"alive" if db_ok else "degraded",
              "database_mode":db_mode, "database_ok":db_ok,
              "render_configured":bool(os.environ.get("SHOTSTACK_API_KEY")),
              "auth_configured":bool(os.environ.get("IA10_BRIDGE_TOKEN")),
              "bootstrap_test_enabled":os.environ.get("IA10_BOOTSTRAP_TEST_RENDER", "").strip().lower() in ("1", "true", "yes"),
              "queue_counts":queue_counts, "failure_categories":failure_categories, "diagnostics_ok":diagnostics_ok,
              "bootstrap_test_render":test_render,
              "note":"Health does not prove provider validity, video QC, or publication."
            })
            return
        if path in ("/queue-summary", "/queue-diagnostics") and self.command == "GET":
            private_diagnostics = path == "/queue-diagnostics"
            if private_diagnostics and not self.authorized():
                return self.send_json(401, {"error":"unauthorized"})
            # Read-only, schema-tolerant queue triage. Postgres JSON conversion lets
            # legacy rows omit optional columns without failing the whole endpoint.
            con = None
            try:
                con = connect_db()
                if isinstance(con, sqlite3.Connection):
                    rows = execute(con, """SELECT id, state, COALESCE(updated_at,0) AS updated_at,
                        CASE
                          WHEN state='QC_PENDING_REVIEW' THEN 'technical_qc_pass_editorial_review_pending'
                          WHEN lower(COALESCE(error,'')) LIKE '%provider_http_403%' THEN 'provider_http_403_forbidden'
                          WHEN lower(COALESCE(error,'')) LIKE '%sandbox key rejected%' THEN 'provider_sandbox_key_rejected'
                          WHEN lower(COALESCE(error,'')) LIKE '%provider_http_%' OR lower(COALESCE(error,'')) LIKE '%shotstack_render_failed%' THEN 'provider_render_error'
                          WHEN lower(COALESCE(error,'')) LIKE '%audio_stream_missing%' THEN 'qc_audio_missing'
                          WHEN lower(COALESCE(error,'')) LIKE '%render_url_%' OR lower(COALESCE(error,'')) LIKE '%download_failed%' THEN 'qc_render_url'
                          WHEN lower(COALESCE(error,'')) LIKE '%decode%' OR lower(COALESCE(error,'')) LIKE '%invalid_or_incomplete%' THEN 'qc_decode'
                          WHEN lower(COALESCE(error,'')) LIKE '%duration_out_of_range%' OR lower(COALESCE(error,'')) LIKE '%not_portrait%' OR lower(COALESCE(error,'')) LIKE '%dimensions_missing%' THEN 'qc_format'
                          WHEN COALESCE(error,'') = '' THEN 'no_error_detail'
                          ELSE 'other'
                        END AS category,
                        (render_url IS NOT NULL AND render_url <> '') AS render_url_available
                        FROM jobs WHERE state IN ('FAILED','QC_FAILED','QC_PENDING_REVIEW')
                        ORDER BY updated_at DESC, id DESC LIMIT 100""").fetchall()
                else:
                    rows = execute(con, """SELECT
                        COALESCE(to_jsonb(j)->>'id','0') AS id,
                        COALESCE(to_jsonb(j)->>'state','UNKNOWN') AS state,
                        COALESCE(to_jsonb(j)->>'updated_at','0') AS updated_at,
                        CASE
                          WHEN COALESCE(to_jsonb(j)->>'state','')='QC_PENDING_REVIEW' THEN 'technical_qc_pass_editorial_review_pending'
                          WHEN POSITION('provider_http_403' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_http_403_forbidden'
                          WHEN POSITION('sandbox key rejected' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('shotstack_sandbox_key_rejected' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_sandbox_key_rejected'
                          WHEN POSITION('provider_response_missing_render_id' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_response_invalid'
                          WHEN POSITION('render_error_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'internal_render_error'
                          WHEN POSITION('provider_http_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('shotstack_render_failed' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'provider_render_error'
                          WHEN POSITION('audio_stream_missing' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_audio_missing'
                          WHEN POSITION('render_url_' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('download_failed' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_render_url'
                          WHEN POSITION('decode' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('invalid_or_incomplete' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_decode'
                          WHEN POSITION('duration_out_of_range' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('not_portrait' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 OR POSITION('dimensions_missing' IN lower(COALESCE(to_jsonb(j)->>'error',''))) > 0 THEN 'qc_format'
                          WHEN COALESCE(to_jsonb(j)->>'error','') = '' THEN 'no_error_detail'
                          ELSE 'other'
                        END AS category,
                        (COALESCE(to_jsonb(j)->>'render_url','') <> '') AS render_url_available
                        FROM jobs j
                        WHERE COALESCE(to_jsonb(j)->>'state','') IN ('FAILED','QC_FAILED','QC_PENDING_REVIEW')
                        ORDER BY CASE WHEN COALESCE(to_jsonb(j)->>'id','') ~ '^[0-9]+$' THEN (to_jsonb(j)->>'id')::bigint ELSE 0 END DESC LIMIT 100""").fetchall()
                jobs = []
                for row in rows:
                    try: job_id = int(row["id"] or 0)
                    except (ValueError, TypeError): job_id = 0
                    try: updated_at = int(row["updated_at"] or 0)
                    except (ValueError, TypeError): updated_at = 0
                    jobs.append({"id":job_id, "state":str(row["state"]), "updated_at":updated_at,
                                 "category":str(row["category"]), "render_url_available":bool(row["render_url_available"])})
                counts = {}
                for job in jobs:
                    key = job["state"] + ":" + job["category"]
                    counts[key] = counts.get(key, 0) + 1
                payload = {"read_only":True, "jobs":jobs, "counts":counts,
                           "note":"No job state changed. QC_PENDING_REVIEW requires human review." if private_diagnostics else
                                  "No job state changed. Only job IDs, states, coarse categories and render-URL availability are exposed."}
                return self.send_json(200, payload)
            except Exception as exc:
                print("queue_readonly_diagnostics_failed endpoint=%s type=%s" % (path, type(exc).__name__), flush=True)
                return self.send_json(503, {"error":"queue_diagnostics_unavailable" if private_diagnostics else "queue_summary_unavailable"})
            finally:
                if con is not None:
                    try: con.close()
                    except Exception: pass

        if path == "/bootstrap-test-video" and self.command == "GET":
            # Public, read-only access to the latest diagnostic render only.
            # Never expose customer jobs or credentials through this convenience route.
            try:
                con = connect_db()
                row = execute(con,
                    "SELECT state,render_url FROM jobs WHERE title LIKE %s ORDER BY id DESC LIMIT 1",
                    ("IA10_BOOTSTRAP_TEST_RENDER%",)).fetchone()
                con.close()
                render_url = str(row["render_url"] or "") if row else ""
                if not row or str(row["state"]) != "QC_PENDING_REVIEW" or not render_url.startswith("https://"):
                    return self.send_json(404, {"error":"diagnostic_video_not_ready"})
                self.send_response(302)
                self.send_header("Location", render_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            except Exception:
                return self.send_json(503, {"error":"diagnostic_video_unavailable"})

        if path.startswith("/media/") and self.command == "GET":
            filename = path[len("/media/"):]
            if not filename or Path(filename).name != filename or not filename.endswith(".mp4"):
                return self.send_json(404, {"error":"not_found"})
            is_diagnostic = filename.startswith("ia10-test-") and filename[len("ia10-test-"):-4].isdigit()
            is_local_render = filename.startswith("local-") and filename[len("local-"):-4].isdigit()
            if not (is_diagnostic or is_local_render):
                return self.send_json(404, {"error":"not_found"})
            media_path = MEDIA_DIR / filename
            try:
                data = media_path.read_bytes()
            except OSError:
                # Render's filesystem is ephemeral and instances do not share files.
                # Recreate only allowlisted diagnostic artifacts or a known local-render
                # job whose stored record still authorizes this exact media filename.
                if is_diagnostic:
                    try:
                        render_local_test_video(filename)
                        data = media_path.read_bytes()
                    except Exception as exc:
                        print("diagnostic_media_regeneration_failed type=%s" % type(exc).__name__, flush=True)
                        return self.send_json(503, {"error":"diagnostic_media_regeneration_failed"})
                elif is_local_render:
                    con = None
                    try:
                        job_id = int(filename[len("local-"):-4])
                        con = connect_db()
                        job = execute(con,
                            "SELECT id,title,state,provider_id,render_url,edit_json FROM jobs WHERE id=%s",
                            (job_id,)).fetchone()
                        if not job:
                            return self.send_json(404, {"error":"media_not_found"})
                        edit_payload = json.loads(job["edit_json"])
                        local_spec = edit_payload.get("local_render") if isinstance(edit_payload, dict) else None
                        expected_url = os.environ.get("IA10_PUBLIC_BASE_URL", "https://ia10-renderer-bridge.onrender.com").rstrip("/") + "/media/" + filename
                        # During technical QC the job is still PROCESSING. Permit access only
                        # after the exact local provider and expected URL have been persisted.
                        # Final-state access retains the same strict provider/URL checks.
                        provider_id = str(job["provider_id"] or "")
                        state = str(job["state"] or "")
                        render_url = str(job["render_url"] or "").rstrip("/")
                        processing_local = (
                            state == "PROCESSING"
                            and provider_id == "local-ffmpeg-edge-tts"
                            and render_url == expected_url
                            and isinstance(local_spec, dict)
                        )
                        completed_local = (
                            state in ("QC_PENDING_REVIEW", "READY_TO_PUBLISH")
                            and provider_id == "local-ffmpeg-edge-tts"
                            and render_url == expected_url
                            and isinstance(local_spec, dict)
                        )
                        if not (processing_local or completed_local):
                            return self.send_json(404, {"error":"media_not_found"})
                        render_local_content(str(job["title"]), local_spec, filename, MEDIA_DIR)
                        data = media_path.read_bytes()
                    except Exception as exc:
                        print("local_media_regeneration_failed type=%s" % type(exc).__name__, flush=True)
                        return self.send_json(503, {"error":"local_media_regeneration_failed"})
                    finally:
                        if con is not None:
                            try:
                                con.close()
                            except Exception:
                                pass
                else:
                    return self.send_json(404, {"error":"media_not_found"})
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/jobs" or path.startswith("/jobs/"):
            if not self.authorized():
                return self.send_json(401, {"error":"unauthorized"})
            try:
                con = connect_db()
                if path == "/jobs":
                    jobs = [dict(r) for r in execute(con,
                      "SELECT id,title,state,created_at,updated_at,provider_id,render_url,error FROM jobs ORDER BY id DESC LIMIT 100")]
                    con.close()
                    return self.send_json(200, {"jobs":jobs})
                raw_id = path[len("/jobs/"):]
                if not raw_id.isdigit():
                    con.close()
                    return self.send_json(400, {"error":"invalid_job_id"})
                job = execute(con,
                  "SELECT id,title,state,created_at,updated_at,provider_id,render_url,error FROM jobs WHERE id=%s",
                  (int(raw_id),)).fetchone()
                con.close()
                if not job:
                    return self.send_json(404, {"error":"job_not_found"})
                return self.send_json(200, {"job":dict(job)})
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

        if path in ("/jobs", "/render"):
            # /render is the documented compatibility endpoint; both routes enqueue safely.
            title, edit = body.get("title", "API_RENDER_REQUEST"), body.get("edit")
            if not isinstance(title, str) or not title.strip() or not isinstance(edit, dict) or not isinstance(edit.get("timeline"), dict) or not isinstance(edit.get("output"), dict):
                return self.send_json(400, {"error":"title_and_shotstack_edit_required"})
            now = int(time.time())
            try:
                con = connect_db()
                row = execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                  (title.strip()[:200], json.dumps(edit), "QUEUED", now, now)).fetchone()
                jid = row["id"]
                con.commit(); con.close()
                return self.send_json(202 if path == "/render" else 201, {"id":jid,"state":"QUEUED","note":"Queued for the background worker; not yet rendered or published."})
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})

        if path.startswith("/jobs/") and path.endswith("/qc"):
            raw_id = path[len("/jobs/"):-len("/qc")].strip("/")
            if not raw_id.isdigit():
                return self.send_json(400, {"error":"invalid_job_id"})
            decision = body.get("decision")
            checklist = body.get("checklist")
            if not validate_editorial_qc(decision, checklist):
                return self.send_json(400, {
                    "error":"editorial_qc_checklist_incomplete",
                    "required_checks":list(EDITORIAL_QC_CHECKS),
                    "note":"Approval requires every check to be explicitly true; this endpoint never publishes."
                })
            if decision == "reject":
                reason = body.get("reason", "")
                if not isinstance(reason, str) or not reason.strip():
                    return self.send_json(400, {"error":"rejection_reason_required"})
                reason = "editorial_qc_rejected: " + " ".join(reason.split())[:300]
            else:
                reason = ""
            try:
                con = connect_db()
                job = execute(con,
                    "SELECT id,title,state,provider_id,render_url,error FROM jobs WHERE id=%s",
                    (int(raw_id),)).fetchone()
                if not job:
                    con.close()
                    return self.send_json(404, {"error":"job_not_found"})
                title = str(job["title"] or "")
                # Diagnostic/bootstrap renders can never be promoted to publishable state.
                if title.startswith("IA10_BOOTSTRAP_TEST_RENDER") or title.startswith("IA10_LOCAL_RENDER_SMOKE_TEST"):
                    con.close()
                    return self.send_json(409, {"error":"diagnostic_jobs_cannot_be_approved"})
                if str(job["state"]) != "QC_PENDING_REVIEW":
                    con.close()
                    return self.send_json(409, {"error":"job_not_pending_editorial_review","state":str(job["state"])})
                if decision == "approve" and (
                    not str(job["provider_id"] or "").strip()
                    or not str(job["render_url"] or "").startswith("https://")
                ):
                    con.close()
                    return self.send_json(409, {"error":"real_provider_render_required"})
                reviewed_at = int(time.time())
                review = {
                    "decision":decision,
                    "checklist":{key: checklist.get(key) is True for key in EDITORIAL_QC_CHECKS},
                    "reviewed_at":reviewed_at,
                }
                new_state = "READY_TO_PUBLISH" if decision == "approve" else "QC_FAILED"
                stored_error = str(job["error"] or "") if decision == "approve" else reason
                cur = execute(con,
                    "UPDATE jobs SET state=%s,error=%s,qc_review_json=%s,updated_at=%s WHERE id=%s AND state='QC_PENDING_REVIEW'",
                    (new_state,stored_error,json.dumps(review,separators=(",",":")),reviewed_at,int(raw_id)))
                if cur.rowcount != 1:
                    con.rollback()
                    con.close()
                    return self.send_json(409, {"error":"job_state_changed_retry_review"})
                con.commit()
                con.close()
                return self.send_json(200, {
                    "id":int(raw_id),"state":new_state,
                    "published":False,
                    "note":"QC decision recorded. READY_TO_PUBLISH is not an instruction to publish automatically."
                })
            except Exception as exc:
                print("editorial_qc_error type=%s" % type(exc).__name__, flush=True)
                return self.send_json(503, {"error":"database_unavailable"})
        
        if path == "/jobs/process-next":
            try:
                con = connect_db()
                queued = execute(con, "SELECT * FROM jobs WHERE state='QUEUED' AND COALESCE(retry_after,0) <= %s ORDER BY id LIMIT 5", (int(time.time()),)).fetchall()
                db_fingerprint = "postgres" if DATABASE_URL else "sqlite"
                if DATABASE_URL:
                    try:
                        import hashlib
                        db_fingerprint = hashlib.sha256(DATABASE_URL.encode("utf-8")).hexdigest()[:12]
                    except Exception:
                        db_fingerprint = "postgres"
                state_rows = execute(con, "SELECT state, COUNT(*) AS count FROM jobs GROUP BY state").fetchall()
                state_counts = {str(row["state"]): int(row["count"]) for row in state_rows}
                failed_rows = execute(con, "SELECT state,error FROM jobs WHERE state IN ('FAILED','QC_FAILED')").fetchall()
                failure_counts = {}
                for failed_row in failed_rows:
                    err = str(failed_row["error"] or "").lower()
                    if "sandbox key rejected" in err or "shotstack_sandbox_key_rejected" in err:
                        category = "provider_sandbox_key_rejected"
                    elif "provider_http_403" in err or "provider_http_401" in err:
                        category = "provider_auth_or_permission"
                    elif "invalid_job_edit_json" in err:
                        category = "invalid_job_edit_json"
                    elif "invalid_job_edit_schema" in err:
                        category = "invalid_job_edit_schema"
                    elif "render_error_" in err:
                        category = "internal_render_error"
                    elif "local_renderer_failed" in err:
                        category = "local_renderer_failed"
                    elif "audio_stream_missing" in err:
                        category = "qc_audio_missing"
                    elif "render_url_" in err or "download_failed" in err:
                        category = "qc_render_url"
                    elif "decode" in err or "invalid_or_incomplete" in err:
                        category = "qc_decode"
                    elif "duration_out_of_range" in err or "not_portrait" in err or "dimensions_missing" in err:
                        category = "qc_format"
                    elif not err:
                        category = "no_error_detail"
                    else:
                        category = "other"
                    failure_counts[category] = failure_counts.get(category, 0) + 1
                print("queue_probe db=%s queued_count_sample=%s first_job_id=%s states=%s failures=%s" % (
                    db_fingerprint, len(queued), str(queued[0]["id"]) if queued else "none",
                    json.dumps(state_counts, sort_keys=True), json.dumps(failure_counts, sort_keys=True)), flush=True)
                job = queued[0] if queued else None
                if not job:
                    con.close()
                    return self.send_json(200, {"processed":False,"note":"queue_empty"})
                execute(con, "UPDATE jobs SET state='PROCESSING',retry_count=COALESCE(retry_count,0)+1,updated_at=%s WHERE id=%s", (int(time.time()),job["id"]))
                con.commit(); con.close()
            except Exception:
                return self.send_json(503, {"error":"database_unavailable"})
            # Validate the stored render payload separately from the provider call.
            # Previously json.loads/shape errors were all reported as render_error_ValueError
            # or render_error_KeyError, obscuring bad queue records as renderer failures.
            try:
                edit_payload = json.loads(job["edit_json"])
            except (TypeError, ValueError):
                con = connect_db()
                execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s AND state='PROCESSING'",
                        ("invalid_job_edit_json", int(time.time()), job["id"]))
                con.commit(); con.close()
                print("queue_job_rejected reason=invalid_job_edit_json", flush=True)
                return self.send_json(422, {"processed":True,"id":job["id"],"state":"FAILED","error":"invalid_job_edit_json"})
            if not isinstance(edit_payload, dict) or not isinstance(edit_payload.get("timeline"), dict) or not isinstance(edit_payload.get("output"), dict):
                con = connect_db()
                execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s AND state='PROCESSING'",
                        ("invalid_job_edit_schema", int(time.time()), job["id"]))
                con.commit(); con.close()
                print("queue_job_rejected reason=invalid_job_edit_schema", flush=True)
                return self.send_json(422, {"processed":True,"id":job["id"],"state":"FAILED","error":"invalid_job_edit_schema"})
            local_spec = edit_payload.get("local_render") if isinstance(edit_payload, dict) else None
            local_enabled = os.environ.get("IA10_LOCAL_RENDER_ENABLED", "").strip().lower() in ("1", "true", "yes")
            if local_enabled and isinstance(local_spec, dict) and not str(job["title"]).startswith("IA10_BOOTSTRAP_TEST_RENDER"):
                try:
                    filename = "local-%s.mp4" % job["id"]
                    render_local_content(job["title"], local_spec, filename, MEDIA_DIR)
                    public_url = os.environ.get("IA10_PUBLIC_BASE_URL", "https://ia10-renderer-bridge.onrender.com").rstrip("/") + "/media/" + filename
                    # Persist the exact provider and URL before QC fetches /media/<id>.
                    # This keeps the in-progress media exception narrow and auditable.
                    con = connect_db()
                    execute(con, "UPDATE jobs SET provider_id=%s,render_url=%s,updated_at=%s WHERE id=%s AND state='PROCESSING'",
                            ("local-ffmpeg-edge-tts", public_url, int(time.time()), job["id"]))
                    con.commit(); con.close()
                    passed, qc_detail = technical_video_qc(public_url)
                    now = int(time.time())
                    new_state = "QC_PENDING_REVIEW" if passed else "QC_FAILED"
                    con = connect_db()
                    execute(con, "UPDATE jobs SET state=%s,error=%s,updated_at=%s WHERE id=%s AND state='PROCESSING'",
                            (new_state, ("local_renderer_" + qc_detail)[:180], now, job["id"]))
                    con.commit(); con.close()
                    print("local_renderer id=%s state=%s qc=%s" % (job["id"], new_state, qc_detail[:120]), flush=True)
                    return self.send_json(200 if passed else 502, {"processed":True,"id":job["id"],"state":new_state,
                        "renderer":"local-ffmpeg-edge-tts","render_url":public_url,"technical_qc":qc_detail,
                        "note":"Technical QC only; editorial review is still required and nothing was published."})
                except Exception as local_exc:
                    print("local_renderer_failed id=%s type=%s" % (job["id"], type(local_exc).__name__), flush=True)
                    con = connect_db()
                    execute(con, "UPDATE jobs SET state='FAILED',error=%s,updated_at=%s WHERE id=%s",
                            ("local_renderer_failed_" + type(local_exc).__name__)[:180], int(time.time()), job["id"])
                    con.commit(); con.close()
                    return self.send_json(502, {"processed":True,"id":job["id"],"state":"FAILED","error":"local_renderer_failed"})
            retryable_failure = False
            try:
                result = submit_render(edit_payload)
                provider_id = (result.get("response") or {}).get("id") or (result.get("data") or {}).get("id") or ""
                if not isinstance(provider_id, str) or not provider_id.strip():
                    raise RuntimeError("provider_response_missing_render_id")
                con = connect_db()
                execute(con, "UPDATE jobs SET state='RENDER_SUBMITTED',provider_id=%s,updated_at=%s WHERE id=%s",
                  (str(provider_id),int(time.time()),job["id"]))
                con.commit(); con.close()
                return self.send_json(200, {"processed":True,"id":job["id"],"state":"RENDER_SUBMITTED","provider_response":result})
            except urllib.error.HTTPError as exc:
                retryable_failure = (exc.code == 429 or exc.code >= 500)
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
                        # A 403 alone does not prove that a sandbox key was rejected:
                        # the provider may deny render permission while allowing template reads.
                        sandbox_rejected = "sandbox key rejected" in detail.lower()
                        safe_error = "provider_sandbox_key_rejected" if sandbox_rejected else "provider_http_403_forbidden" if exc.code == 403 else "provider_http_%s" % exc.code
                        con = connect_db()
                        execute(con, "UPDATE jobs SET state=%s,render_url=%s,error=%s,updated_at=%s WHERE id=%s",
                                ("FAILED", public_url, ("Provider render rejected; local diagnostic MP4 is not a production render. " + detail)[:180], int(time.time()), job["id"]))
                        con.commit(); con.close()
                        return self.send_json(502, {"processed":True,"id":job["id"],"state":"FAILED",
                            "renderer":"local-test-fallback","diagnostic_url":public_url,
                            "error":safe_error,
                            "detail":"A local diagnostic MP4 was generated, but the provider render failed. This job is not approved for publication."})
                    except Exception as fallback_exc:
                        # Log a short diagnostic without exposing credentials or request headers.
                        print("diagnostic_fallback_error type=%s detail=%s" % (type(fallback_exc).__name__, str(fallback_exc)[:240]), flush=True)
                        detail += "; local_test_fallback_failed_" + type(fallback_exc).__name__
            except Exception as exc:
                retryable_failure = isinstance(exc, urllib.error.URLError)
                detail = "render_error_" + type(exc).__name__
                # Traceback gives the failing code location; do not log locals, headers, or credentials.
                print("render_exception_traceback\\n" + traceback.format_exc()[-3000:], flush=True)
            retry = retryable_failure and int(job["retry_count"] or 0) + 1 < 4
            retry_delay = min(900, 60 * (2 ** max(0, int(job["retry_count"] or 0)))) if retry else 0
            new_state = "QUEUED" if retry else "FAILED"
            now = int(time.time())
            safe_detail = ("auto_retry_after_%ss:" % retry_delay + detail)[:180] if retry else detail[:180]
            try:
                con = connect_db()
                execute(con, "UPDATE jobs SET state=%s,error=%s,retry_after=%s,updated_at=%s WHERE id=%s",
                  (new_state,safe_detail,now + retry_delay if retry else 0,now,job["id"]))
                con.commit(); con.close()
                if retry:
                    print("queue_auto_retry id=%s attempt=%s delay_seconds=%s" %
                          (job["id"], int(job["retry_count"] or 0) + 1, retry_delay), flush=True)
            except Exception as retry_exc:
                print("queue_retry_policy_error type=%s" % type(retry_exc).__name__, flush=True)
            return self.send_json(502, {"error":"render_failed","id":job["id"],"detail":detail,
                "retry_scheduled":retry,"retry_after_seconds":retry_delay})

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
        # Fetch the provider output first, then decode the local file. Some signed
        # render URLs/redirects are not handled consistently by static FFmpeg builds.
        # Never log the URL because it may contain temporary access signatures.
        with tempfile.NamedTemporaryFile(prefix="ia10-qc-", suffix=".mp4", delete=False) as tmp:
            local_path = tmp.name
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "IA10-Technical-QC/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response, open(local_path, "wb") as output_file:
                shutil.copyfileobj(response, output_file)
            if os.path.getsize(local_path) < 1024:
                return False, "render_file_too_small"
            proc = subprocess.run([ffmpeg, "-v", "info", "-i", local_path, "-f", "null", "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            output = ((proc.stderr or b"") + (proc.stdout or b"")).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                return False, "render_url_http_403"
            if exc.code == 404:
                return False, "render_url_http_404"
            if exc.code >= 500:
                return False, "render_url_http_5xx"
            return False, "render_url_http_error"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print("technical_qc_download_error type=%s" % type(exc).__name__, flush=True)
            return False, "render_url_download_failed"
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass
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
            # Diagnose fetch/codec failures without logging the signed URL or its query string.
            low = output.lower()
            if "http error 403" in low or "403 forbidden" in low:
                return False, "render_url_http_403"
            if "http error 404" in low or "404 not found" in low:
                return False, "render_url_http_404"
            if "http error 5" in low or "server returned 5" in low:
                return False, "render_url_http_5xx"
            if any(marker in low for marker in ("http error", "403 forbidden", "404 not found", "server returned", "connection refused", "connection timed out", "connection reset", "tls error", "ssl error", "error opening input", "failed to open", "network is unreachable", "temporary failure in name resolution")):
                # Keep a few sanitized FFmpeg error lines for diagnosis; strip signed URLs/tokens.
                import re
                safe_lines = []
                diagnostic_markers = (
                    "http error", "server returned", "403 forbidden", "404 not found",
                    "timed out", "connection refused", "connection reset", "tls error",
                    "error opening input", "failed to open", "moov atom not found",
                    "invalid data found", "could not find codec parameters",
                    "protocol not found", "operation not permitted", "network is unreachable"
                )
                output_lines = output.splitlines()
                for line in output_lines:
                    ll = line.lower()
                    if any(marker in ll for marker in diagnostic_markers):
                        line = re.sub(r"https?://[^\\s'\"<>]+", "[URL_REDACTED]", line)
                        line = re.sub(r"(?i)(token|signature|key)=([^&\\s]+)", r"\\1=[REDACTED]", line)
                        safe_lines.append(line[:180])
                # Some FFmpeg builds phrase fetch failures differently. If no known marker
                # matched, emit only sanitized lines that look like errors, never the signed URL.
                if not safe_lines:
                    fallback_markers = ("error", "failed", "denied", "refused", "timeout", "timed out",
                                        "invalid", "http", "opening", "server", "tls", "ssl", "network")
                    for line in output_lines:
                        if any(marker in line.lower() for marker in fallback_markers):
                            line = re.sub(r"https?://[^\\s'\"<>]+", "[URL_REDACTED]", line)
                            line = re.sub(r"(?i)(token|signature|key)=([^&\\s]+)", r"\\1=[REDACTED]", line)
                            safe_lines.append(line[:180])
                print("technical_qc_fetch_detail " + " | ".join(safe_lines[-5:])[:600], flush=True)
                return False, "render_url_fetch_failed"
            if "moov atom not found" in low or "invalid data found" in low or "could not find codec parameters" in low:
                return False, "render_file_invalid_or_incomplete"
            # Emit a sanitized diagnostic for other decoder failures too; never log signed URLs.
            safe_lines = []
            diagnostic_markers = ("error", "failed", "denied", "refused", "timeout", "timed out",
                                  "invalid", "http", "opening", "server", "tls", "ssl", "network",
                                  "protocol", "not found", "forbidden", "unreachable", "permission")
            for line in output.splitlines():
                if any(marker in line.lower() for marker in diagnostic_markers):
                    line = re.sub(r"https?://[^\\s'\"<>]+", "[URL_REDACTED]", line)
                    line = re.sub(r"(?i)(token|signature|key)=([^&\\s]+)", r"\\1=[REDACTED]", line)
                    safe_lines.append(line[:180])
            if safe_lines:
                print("technical_qc_decode_detail " + " | ".join(safe_lines[-5:])[:600], flush=True)
            return False, "ffmpeg_decode_failed"
        if not dimensions:
            return False, "video_stream_or_dimensions_missing"
        # Publishing pipeline expects audible content; do not pass silent MP4s through technical QC.
        if not re.search(r"\bAudio:", output, re.IGNORECASE):
            return False, "audio_stream_missing"
        width, height = dimensions
        if height <= width:
            return False, "not_portrait"
        if not 4 <= duration <= 15:
            return False, "duration_out_of_range"
        return True, "decoded_portrait_%sx%s_duration_%.2fs_audio_stream_present" % (width, height, duration)
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
        job = execute(con, "SELECT id,provider_id,state,render_url,title FROM jobs WHERE state='RENDER_SUBMITTED' AND provider_id IS NOT NULL AND provider_id<>'' ORDER BY updated_at LIMIT 1").fetchone()
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

def recover_stale_processing_jobs():
    """Requeue work left PROCESSING after a worker/process interruption."""
    now = int(time.time())
    # Local renders take seconds; provider requests have bounded timeouts.
    # A 15-minute grace period avoids touching work that may still be active.
    cutoff = now - max(900, int(os.environ.get("IA10_STALE_PROCESSING_SECONDS", "900")))
    con = connect_db()
    try:
        cur = execute(
            con,
            "UPDATE jobs SET state='QUEUED',error=%s,updated_at=%s "
            "WHERE state='PROCESSING' AND updated_at < %s",
            ("requeued_after_stale_processing", now, cutoff))
        count = cur.rowcount
        con.commit()
        if count:
            print("queue_recovery requeued_stale_processing=%s grace_seconds=%s" %
                  (count, now - cutoff), flush=True)
        return count
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
                        "type": "video",
                        "src": "https://samplelib.com/lib/preview/mp4/sample-5s.mp4",
                        "volume": 1
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
            if existing["state"] in ("FAILED", "QC_FAILED") and (
                "render_error_KeyError" in existing_error or
                "shotstack_sandbox_key_rejected" in existing_error or
                "Shotstack Sandbox request rejected" in existing_error or
                "render_url_fetch_failed" in existing_error or
                "ffmpeg_decode_failed" in existing_error or
                "render_file_invalid_or_incomplete" in existing_error
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

def local_renderer_smoke_test_once():
    """Queue one non-publishable local-render smoke test when explicitly enabled."""
    if os.environ.get("IA10_LOCAL_RENDER_SMOKE_TEST", "").strip().lower() not in ("1", "true", "yes"):
        return
    run_id = os.environ.get("IA10_LOCAL_RENDER_SMOKE_TEST_RUN_ID", "V1").strip()[:40] or "V1"
    run_id = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_") or "V1"
    title = "IA10_LOCAL_RENDER_SMOKE_TEST_" + run_id
    edit = {
        "timeline": {"tracks": []},
        "output": {"format": "mp4", "resolution": "1080", "aspectRatio": "9:16"},
        "local_render": {
            "headline": "Prueba real del renderizador local",
            "script": "Esta es una prueba técnica del circuito IA tres punto cero. Comprobamos voz española, vídeo vertical, audio integrado y validación automática antes de cualquier revisión editorial.",
            "source": "PRUEBA INTERNA; NO ES NOTICIA"
        }
    }
    con = connect_db()
    try:
        existing = execute(con, "SELECT id,state FROM jobs WHERE title=%s LIMIT 1", (title,)).fetchone()
        if existing:
            print("local_renderer_smoke already_exists id=%s state=%s" % (existing["id"], existing["state"]), flush=True)
            return
        now = int(time.time())
        row = execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                      (title, json.dumps(edit), "QUEUED", now, now)).fetchone()
        con.commit()
        print("local_renderer_smoke queued id=%s" % row["id"], flush=True)
    finally:
        con.close()

def has_queued_local_render_job():
    """Read-only check allowing local jobs to run without a working Shotstack key."""
    con = connect_db()
    try:
        rows = execute(con, "SELECT edit_json FROM jobs WHERE state='QUEUED' ORDER BY id LIMIT 25").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["edit_json"])
                if isinstance(payload, dict) and isinstance(payload.get("local_render"), dict):
                    return True
            except Exception:
                continue
        return False
    finally:
        con.close()

def rss_intake_worker():
    """Periodically enqueue up to five sourced Spanish items per rolling day for review."""
    interval = max(60, int(os.environ.get("IA10_RSS_INTERVAL_SECONDS", "60")))
    while True:
        try:
            ingest_rss_once(connect_db, execute)
        except Exception as exc:
            print("rss_ingest_worker_error type=%s" % type(exc).__name__, flush=True)
        time.sleep(interval)

def background_queue_worker():
    """Poll the guarded queue while this free web service is awake."""
    interval = max(15, int(os.environ.get("IA10_WORKER_INTERVAL_SECONDS", "30")))
    print("queue_worker_started interval=%s provider_key_configured=%s auth_token_configured=%s" % (
        interval, bool(os.environ.get("SHOTSTACK_API_KEY", "").strip()),
        bool(os.environ.get("IA10_BRIDGE_TOKEN", ""))), flush=True)
    while True:
        time.sleep(interval)
        # Recover jobs abandoned by a crashed/interrupted request before selecting new work.
        # Only rows older than the conservative grace period are eligible.
        try:
            recover_stale_processing_jobs()
        except Exception as exc:
            print("queue_recovery_error type=%s action=retry_next_cycle" % type(exc).__name__, flush=True)
        # Prefer the local FFmpeg fallback whenever a sourced local job is queued.
        # A transient database exception must not permanently kill this daemon thread.
        try:
            local_pending = has_queued_local_render_job()
        except Exception as exc:
            print("queue_worker_guard_error type=%s action=retry_next_cycle" % type(exc).__name__, flush=True)
            continue
        provider_key = os.environ.get("SHOTSTACK_API_KEY", "").strip()
        if provider_key and not local_pending:
            try:
                poll_submitted_render()
            except Exception as exc:
                print("render_poll_worker_error type=%s" % type(exc).__name__, flush=True)
        elif not provider_key and not local_pending:
            continue
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

if __name__ == "__main__":
    connect_db().close()
    check_shotstack_credentials()
    recover_interrupted_bootstrap_jobs()
    recover_stale_processing_jobs()
    bootstrap_test_render_once()
    local_renderer_smoke_test_once()
    threading.Thread(target=background_queue_worker, name="ia10-queue-worker", daemon=True).start()
    threading.Thread(target=rss_intake_worker, name="ia10-rss-intake", daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()