"""Conservative RSS-to-local-render intake for Spanish-language editorial review.

Only creates QUEUED jobs with source URLs and a short derived summary. It never publishes.
"""
import email.utils
import hashlib
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_FEEDS = [
    "https://www.rtve.es/rss/temas_noticias.xml",
    "https://www.rtve.es/rss/temas_deportes.xml",
    "https://www.rtve.es/rss/temas_ciencia-tecnologia.xml",
    "https://www.rtve.es/rss/temas_cultura.xml",
    "https://www.rtve.es/rss/temas_navarra.xml",
    "https://www.rtve.es/rss/temas_tiempo.xml",
]

def _clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return " ".join(value.split())

def _tag(element, suffix):
    for child in element.iter():
        if child.tag.lower().endswith(suffix.lower()):
            return (child.text or "").strip()
    return ""

def _feed_items(feed_url):
    request = urllib.request.Request(feed_url, headers={"User-Agent": "IA10-RSS-Intake/1.0", "Accept": "application/rss+xml, application/atom+xml, application/xml"})
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read(1_500_000)
    root = ET.fromstring(raw)
    now = datetime.now(timezone.utc)
    for item in root.iter():
        if not (item.tag.lower().endswith("item") or item.tag.lower().endswith("entry")):
            continue
        title = _clean(_tag(item, "title"))
        summary = _clean(_tag(item, "description") or _tag(item, "summary") or _tag(item, "encoded") or _tag(item, "content"))
        link = _tag(item, "link")
        if link and not link.startswith(("http://", "https://")):
            link = ""
        # RTVE's published RSS items may still use legacy HTTP links; upgrade only its own host.
        if link.startswith("http://www.rtve.es/") or link.startswith("http://api2.rtve.es/"):
            link = "https://" + link[len("http://"):]
        if not link:
            for child in item:
                if child.tag.lower().endswith("link"):
                    candidate = child.attrib.get("href", "")
                    if candidate.startswith("https://"):
                        link = candidate
                        break
        pub = _tag(item, "pubDate") or _tag(item, "published") or _tag(item, "updated") or _tag(item, "date")
        try:
            published = None
            if pub:
                try:
                    published = email.utils.parsedate_to_datetime(pub)
                except Exception:
                    published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published and (now - published.astimezone(timezone.utc)).total_seconds() > 36 * 3600:
                continue
            if published and (published.astimezone(timezone.utc) - now).total_seconds() > 2 * 3600:
                continue
        except Exception:
            continue
        if title and link.startswith("https://"):
            yield title[:110], summary[:260], link[:1000]

def ingest_rss_once(connect_db, execute):
    if os.environ.get("IA10_RSS_INGEST_ENABLED", "true").strip().lower() not in ("1", "true", "yes"):
        return
    if os.environ.get("IA10_LOCAL_RENDER_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        print("rss_ingest skipped reason=local_renderer_disabled", flush=True)
        return
    configured = os.environ.get("IA10_RSS_FEEDS", "").strip()
    feeds = [x.strip() for x in configured.split(";") if x.strip()] if configured else DEFAULT_FEEDS
    now = int(time.time())
    con = connect_db()
    created = 0
    skipped = 0
    errors = 0
    try:
        recent = execute(con, "SELECT COUNT(*) AS count FROM jobs WHERE title LIKE %s AND created_at >= %s",
                         ("IA10_RSS_%", now - 86400)).fetchone()
        daily_count = int(recent["count"] or 0)
        if daily_count >= 5:
            print("rss_ingest skipped reason=daily_cap count=%s" % daily_count, flush=True)
            return
        for feed_url in feeds:
            if created + daily_count >= 5:
                break
            try:
                items = list(_feed_items(feed_url))
                print("rss_ingest_feed parsed_items=%s" % len(items), flush=True)
                for headline, summary, source_url in items:
                    if created + daily_count >= 5:
                        break
                    digest = hashlib.sha256((source_url + "|" + headline).encode("utf-8")).hexdigest()[:12]
                    title = "IA10_RSS_%s %s" % (digest, headline)
                    exists = execute(con, "SELECT id FROM jobs WHERE title=%s LIMIT 1", (title,)).fetchone()
                    if exists:
                        skipped += 1
                        continue
                    excerpt = summary or "La fuente ha publicado esta información; consulta el enlace original para comprobar los detalles."
                    script = ("Según la fuente enlazada, " + headline + ". " + excerpt +
                              " Consulta la publicación original para contexto y actualización. Este resumen automático requiere revisión editorial antes de difundirse.")
                    script = " ".join(script.split())[:1700]
                    if len(script.split()) < 8:
                        script += " La fuente original está enlazada en este vídeo para su verificación."
                    edit = {
                        "timeline": {"tracks": []},
                        "output": {"format": "mp4", "resolution": "1080", "aspectRatio": "9:16"},
                        "local_render": {
                            "headline": headline,
                            "script": script,
                            "source": source_url
                        }
                    }
                    stamp = int(time.time())
                    execute(con, "INSERT INTO jobs(title,edit_json,state,created_at,updated_at) VALUES(%s,%s,%s,%s,%s)",
                            (title, json.dumps(edit, ensure_ascii=False), "QUEUED", stamp, stamp))
                    con.commit()
                    created += 1
            except Exception as exc:
                errors += 1
                print("rss_ingest_feed_error type=%s" % type(exc).__name__, flush=True)
        print("rss_ingest finished created=%s skipped=%s feed_errors=%s" % (created, skipped, errors), flush=True)
    finally:
        con.close()
