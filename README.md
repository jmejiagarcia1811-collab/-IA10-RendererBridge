# IA10 Renderer Bridge (public deployment copy)

Minimal HTTP bridge for submitting Shotstack Edit API render jobs.

## Security
- No API keys or bearer tokens are stored in this repository.
- Set `SHOTSTACK_API_KEY` as a secret environment variable in Render.
- Render generates `IA10_BRIDGE_TOKEN`; use it as a bearer token when calling `POST /render`.
- `GET /health` exposes only whether required settings are present, never their values.
- This is a deployment copy; the original project repository remains private.

## Deploy on Render
1. Create a Blueprint from this repository and review `render.yaml`.
2. Add the Shotstack Sandbox API key to the `SHOTSTACK_API_KEY` secret field.
3. Deploy and verify `/health`.
4. Only test a real render after confirming the key and request payload.

## API
- `GET /health`: service and configuration status.
- `POST /render`: validates the Shotstack edit payload and queues it for the background worker; returns HTTP 202 with a job ID. Requires `Authorization: Bearer <IA10_BRIDGE_TOKEN>`.
- `POST /jobs`: same queue, with optional `title`; returns HTTP 201 with a job ID.
- `GET /jobs`: lists recent jobs (authorized).
- `GET /jobs/{id}`: checks one job (authorized).

A healthy endpoint does not mean rendering or social publishing is configured.

## Guarded local narrated fallback

The optional local renderer can create a vertical 1080x1920 MP4 with Spanish neural narration from an explicit `edit.local_render` payload:

```json
{
  "title": "Headline test",
  "edit": {
    "timeline": {"tracks": []},
    "output": {"format": "mp4", "resolution": "1080", "aspectRatio": "9:16"},
    "local_render": {
      "headline": "Headline text supplied by the content pipeline",
      "script": "A factual Spanish narration of at least eight words, supplied by the verified content pipeline.",
      "source": "https://example.org/source"
    }
  }
}
```

Enable with Render environment variable `IA10_LOCAL_RENDER_ENABLED=true`. It uses edge-tts neural voice `es-ES-AlvaroNeural` by default and FFmpeg for H.264/AAC MP4 output. It requires outbound access to the speech service. This renderer produces a narrated title card, not source footage; facts and source must come from the upstream verifier. Output is sent to `QC_PENDING_REVIEW` only after technical decode, portrait, duration and audio checks pass. It never publishes automatically. Render's local filesystem is ephemeral, so generated files are not durable across restarts/deploys; store approved assets in durable object storage before relying on them for scheduling.
