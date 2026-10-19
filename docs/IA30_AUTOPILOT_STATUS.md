# IA 3.0 — Autopilot execution status
Updated: 2026-10-19 12:12 UTC

## Operating rules
- Keep the existing Render service and PostgreSQL resource; do not create duplicates.
- Keep the project on Render Free and target 0 € recurring cost.
- Never commit credentials, print secrets, or ask the owner to paste keys into chat.
- Do not publish to social platforms from this bridge. A rendered job is not publication-ready.
- Keep real publishing blocked until technical QC, source attribution, on-screen text review, and human editorial QA are verified.
- Distinguish verified results from estimates; never report revenue unless it is actually received.

## Current infrastructure (verified)
- Render service: `ia10-renderer-bridge`
- URL: https://ia10-renderer-bridge.onrender.com
- Service ID: `srv-db8v6ocs728c73b73t50`
- Repository branch: `main`; automatic deploy is disabled.
- Plan: Free; one service instance; Frankfurt region.
- PostgreSQL resource: `ia10-control-queue`, Free plan; expiry previously reported as 2026-11-15. Plan a continuity check before expiry without opening the DB to broad IP ranges.
- Latest deployment commit: `8a620b829bd7f0444c66895a555d29ae1869a527`; Render events show deployment succeeded and service live at 2026-10-19 12:06 UTC.
- App logs show a PostgreSQL fingerprint (`queue_probe db=5784ad06bd7c`) and the worker successfully querying the queue. Earlier health output reported `database_mode=postgres` and `database_ok=true`.
- Logs show provider authentication check `status=200 result=ok`; never log or disclose the API key.

## Verified rendering evidence
- Diagnostic job ID 20 reached `QC_PENDING_REVIEW`.
- Render poll log at 2026-10-19 12:05:45 UTC reports `decoded_portrait_1080x1920_duration_5.01s_audio_stream_present`.
- This is evidence of a real provider render artifact passing the bridge's technical checks, not evidence of editorial approval or publication.
- The bootstrap helper reported the matching diagnostic already existed, so do not claim the environment-variable update created a second job.
- The worker subsequently returned HTTP 200 with `queue_empty`; this confirms polling is alive but no ordinary content job was waiting.
- A second deployment was triggered during the previous troubleshooting session. The latest deployment is live; the earlier deployment is deactivated. Avoid further deploys unless code/config changes require one.

## Current workflow and acceptance gates
1. **Preflight:** service responds, health returns 200, PostgreSQL connected, auth configured, provider key present; never expose secret values.
2. **Provider check:** API base and credential environment match (Sandbox/stage vs production/v1). A 403 is a hard failure; do not retry indefinitely or switch environments blindly.
3. **Single diagnostic job:** create one clearly labelled test render only when needed; no user-facing content or publication.
4. **Render poll:** persist provider ID and poll to terminal state; capture safe provider diagnostics.
5. **Artifact QC:** verify real provider MP4, decodability, 1080x1920 / 9:16, duration 4–15s, audio where required. Local fallback does not satisfy this gate.
6. **Editorial gate:** source/date, factual accuracy, spelling, readable overlays, rights, and human approval must be checked before READY_TO_PUBLISH.
7. **Persistence/recovery:** verify job survives service restart/redeploy and remains in PostgreSQL; recover interrupted jobs idempotently.
8. **Content intake:** connect and test radar → verifier → content assembly → queue. This upstream intake is not demonstrated by this renderer repository's current runtime logs.
9. **Publication/analytics:** verify Metricool/platform connection and a safe draft/test workflow. Publishing remains disabled until a human explicitly approves the connected workflow.
10. **Reporting:** record timestamp, job ID, provider state, artifact URL (without temporary signed query strings), checks passed/failed, and cost tier; no secrets.

## Remaining blockers
- No ordinary content jobs have been observed entering the queue; the worker is polling an empty queue.
- The upstream radar, fact-checking, and Spanish content-generation circuit is not proven connected to this bridge.
- Persistence across a service restart/redeploy has not yet been demonstrated end to end.
- Editorial QA and the `READY_TO_PUBLISH` gate are not verified end to end.
- Metricool publishing and analytics have not been tested in this run; no real posts have been sent.
- The PostgreSQL Free expiry date needs a continuity plan before 2026-11-15.

## Current status
**Renderer: diagnostic cloud render technically verified. Full IA 3.0 autopilot: still open.**
No real social publication should be sent until upstream intake, editorial QA, persistence, and publication controls are verified.
