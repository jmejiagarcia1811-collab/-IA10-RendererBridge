"""Cost-conscious local renderer for explicit, script-based fallback jobs.

This renderer creates a narrated vertical headline card, not fabricated footage.
It requires an explicit local_render payload and never approves publication.
"""
import os
import subprocess
import textwrap
from pathlib import Path

def render_local_content(title, spec, filename, media_dir):
    if not isinstance(spec, dict):
        raise ValueError("local_render_spec_required")
    headline = " ".join(str(spec.get("headline") or title or "").split())[:110]
    script = " ".join(str(spec.get("script") or "").split())[:1800]
    source = " ".join(str(spec.get("source") or "").split())[:160]
    if len(script.split()) < 8:
        raise ValueError("local_render_script_too_short")
    if not headline:
        raise ValueError("local_render_headline_required")
    import asyncio
    from PIL import Image, ImageDraw, ImageFont
    import imageio_ffmpeg
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    output = media_dir / filename
    audio = media_dir / (filename[:-4] + ".mp3")
    image = media_dir / (filename[:-4] + ".png")
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        font_head = ImageFont.truetype(font_path, 66)
        font_body = ImageFont.truetype(regular_path, 43)
        font_small = ImageFont.truetype(regular_path, 28)
    except OSError:
        font_head = font_body = font_small = ImageFont.load_default()
    canvas = Image.new("RGB", (1080, 1920), (12, 20, 32))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((64, 170, 1016, 1745), radius=44, fill=(20, 35, 52), outline=(50, 190, 180), width=5)
    draw.text((110, 235), "IA 3.0  •  ACTUALIDAD", font=font_small, fill=(65, 220, 205))
    def draw_wrapped(text, font, x, y, max_width, max_y, fill, line_gap=18):
        # Wrap by actual rendered pixel width and stop before the reserved footer.
        words = text.split()
        lines, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        line_height = max(1, font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + line_gap
        max_lines = max(1, (max_y - y) // line_height)
        truncated = len(lines) > max_lines
        lines = lines[:max_lines]
        if truncated and lines:
            tail = lines[-1]
            while tail and draw.textbbox((0, 0), tail + "…", font=font)[2] > max_width:
                tail = tail[:-1]
            lines[-1] = tail.rstrip() + "…"
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
        return y
    y = draw_wrapped(headline, font_head, 110, 350, 860, 760, (255,255,255), 24)
    divider_y = min(y + 24, 900)
    draw.line((110, divider_y, 900, divider_y), fill=(65,220,205), width=5)
    body_y = min(divider_y + 65, 950)
    y = draw_wrapped(script, font_body, 110, body_y, 860, 1380, (225,232,240), 18)
    if source:
        draw_wrapped("Fuente: " + source, font_small, 110, max(y + 24, 1430), 860, 1580, (255,190,90), 10)
    draw.text((110, 1660), "REVISIÓN EDITORIAL PENDIENTE", font=font_small, fill=(255,190,90))
    canvas.save(image, format="PNG")
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        # Smoke tests use the same Spanish neural TTS path as real content.
        # Never treat a silent audio stream as proof that narration works.
        subprocess.run([
            os.environ.get("PYTHON", "python"), "-m", "edge_tts",
            "--voice", os.environ.get("IA10_TTS_VOICE", "es-ES-AlvaroNeural"),
            "--text", script, "--write-media", str(audio)
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
        subprocess.run([
            ffmpeg, "-y", "-loop", "1", "-framerate", "24", "-i", str(image),
            "-i", str(audio), "-vf", "scale=1080:1920,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-t", "15",
            "-movflags", "+faststart", str(output)
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    finally:
        for tmp in (audio, image):
            try:
                tmp.unlink()
            except OSError:
                pass
    if not output.exists() or output.stat().st_size < 1024:
        raise RuntimeError("local_render_output_invalid")
    return output
