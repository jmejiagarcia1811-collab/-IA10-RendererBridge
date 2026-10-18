# IA 3.0 — Autopilot execution status
Updated: 2026-10-18 (UTC)

## Operating rules
- Keep the existing Render service and PostgreSQL resource; do not create duplicates.
- Keep the project on Render Free unless explicitly approved otherwise.
- Never commit credentials, print secrets, or ask the owner to paste keys into chat.
- Do not publish to social platforms from this bridge. A rendered job is not publication-ready.
- Keep real publishing blocked until render URL, video integrity, vertical dimensions, audio/text, source attribution, and editorial QA are verified.
- Distinguish verified results from estimates; never report revenue unless it is actually received.

## Observed deployment
- Service: `ia10-renderer-bridge`
- Service URL: https://ia10-renderer-bridge.onrender.com
- Render service ID: `srv-db8v6ocs728c73b73t50`
- Repository branch: `main`; auto-deploy is disabled.
- PostgreSQL resource: `ia10-control-queue`, Free plan, status reported available, expiry reported as 2026-11-15.
- Latest inspected deployment was marked live. App logs show periodic `POST /jobs/process-next` requests returning HTTP 200 with `queue_empty`; this confirms worker polling, not successful processing of a real job.
- External database diagnostic was blocked by an empty IP allowlist. Do not open the database to all IPs merely to make the hosted diagnostic connector work. Verify the service's own `/health` output and app connection instead.
- Code's health endpoint reports `database_mode=sqlite-local-ephemeral` when `DATABASE_URL` is absent. The target state is PostgreSQL, not ephemeral SQLite.
- Shotstack test has previously been rejected with HTTP 403. No verified successful Shotstack render has yet been established by this check.
- The fixed local MP4 is diagnostic fallback only. It must never be presented as a successful Shotstack render or approved content.

## Automated workflow / acceptance gates
1. **Preflight**: service responds, `/health` returns 200, `database_mode=postgres`, `database_ok=true`, auth configured, provider key present. Never log secret values.
2. **Provider check**: verify the Shotstack API base matches the credential's environment (Sandbox/stage vs production/v1). A 403 is a hard failure; do not retry endlessly or switch environments blindly.
3. **Single test job**: enqueue one clearly labelled test render only after preflight; no user-facing content or publication.
4. **Render poll**: persist provider ID and poll until terminal state; capture provider error safely; use bounded retries/backoff.
5. **Artifact checks**: verify a real MP4 is reachable, decodable, 1080x1920 / 9:16, expected duration, audio where required, and no truncation. A local fallback does not satisfy this gate.
6. **QA state**: use a distinct review/QA gate before READY_TO_PUBLISH; do not auto-publish on render completion.
7. **Persistence/recovery**: restart/redeploy and verify jobs remain in PostgreSQL; recover interrupted jobs safely and idempotently.
8. **Reporting**: record timestamp, job ID, render provider status, artifact URL, checks passed/failed, and cost tier; no secrets.
9. **Publication**: remains disabled until all gates pass and a human explicitly enables the connected publishing workflow.

## Immediate blockers
- Confirm runtime uses PostgreSQL rather than `sqlite-local-ephemeral`.
- Resolve Shotstack 403 by confirming the credential's environment and matching `SHOTSTACK_API_BASE`.
- Run one successful provider render and validate the returned artifact.
- Add real video QC and persistence/restart evidence.
- Only then integrate scheduled content ingestion, factual verification, Metricool publishing, and analytics.

## Current status
**Diagnostic only — not production-ready.** Worker polling is observed; database persistence and successful provider render are not yet verified. No social post should be sent.
