# IA 3.0 — Autopilot execution status
Updated: 2026-10-19 17:13 UTC

## Operating rules
- Keep the existing Render service and PostgreSQL resource; do not create duplicates.
- Keep the project on Render Free and target 0 € recurring cost.
- Never commit credentials, print secrets, or ask the owner to paste keys into chat.
- Do not publish to social platforms from this bridge. A rendered job is not publication-ready.
- Keep real publishing blocked until technical QC, source attribution, on-screen text review, rights checks, and human editorial QA are verified.
- Distinguish verified results from estimates; never report revenue unless it is actually received.

## Current infrastructure (verified)
- Render service: `ia10-renderer-bridge`
- URL: https://ia10-renderer-bridge.onrender.com
- Service ID: `srv-db8v6ocs728c73b73t50`
- Repository: private GitHub repository `jmejiagarcia1811-collab/-IA10-RendererBridge`, branch `main`.
- Render plan: Free; one service instance; Frankfurt region. Automatic deploy is disabled; a code commit does not itself deploy.
- PostgreSQL resource: `ia10-control-queue`, Free plan; expiry previously reported as 2026-11-15. Plan a continuity check before expiry without opening the DB to broad IP ranges.
- Service details query at 2026-10-19 17:13 UTC reports active/not suspended and Free plan.
- App logs at 17:10–17:12 UTC show repeated successful HTTP 200 queue-worker polls and PostgreSQL fingerprint `5784ad06bd7c`; the queue has zero QUEUED jobs.
- `/health` returned HTTP 200 at 17:10 UTC. Provider authentication had previously returned status 200. Never log or disclose key values.

## Verified rendering evidence
- A new diagnostic run was triggered with run ID `20261019-1707`.
- Diagnostic job ID 21 reached `QC_PENDING_REVIEW`.
- Render poll log at 2026-10-19 17:09 UTC reports `decoded_portrait_1080x1920_duration_5.01s_audio_stream_present`.
- This confirms a real provider-rendered portrait MP4 with audio passed the bridge's technical checks. It does not confirm editorial approval or publication.
- Diagnostic jobs cannot be approved through editorial QC and must remain unpublished.
- Worker polling is alive, but ordinary content queue remains empty.

## Metricool / analytics evidence
- Metricool brand `7358318` is connected for TikTok and YouTube.
- A technical draft was previously found in the planner for 2026-10-20 12:00 local time, with draft enabled, automatic publishing disabled, YouTube privacy private, and TikTok privacy SELF_ONLY. Recheck its state before any future change; do not convert it to public content.
- Read-only analytics endpoints responded for TikTok posts and YouTube. Returned historical rows include zero values for YouTube revenue fields in sampled rows; this is not evidence of income. Analytics retrieval is not yet integrated into an automated feedback loop.

## Acceptance gates
1. Preflight: service responds, health returns 200, PostgreSQL connected, auth configured, provider key present; never expose secret values.
2. Provider check: API base and credential environment match (Sandbox/stage vs production/v1). A 403 is a hard failure; do not retry indefinitely or switch environments blindly.
3. Diagnostic render: create one clearly labelled test only when needed; no user-facing content or publication.
4. Render poll: persist provider ID and poll to terminal state; capture safe provider diagnostics.
5. Artifact QC: verify real provider MP4, decodability, 1080x1920 / 9:16, duration 4–15s, audio where required. Local fallback does not satisfy this gate.
6. Editorial gate: source/date, factual accuracy, spelling, readable overlays, rights, and human approval must be checked before READY_TO_PUBLISH.
7. Persistence/recovery: verify job survives service restart/redeploy and remains in PostgreSQL; recover interrupted jobs idempotently.
8. Content intake: connect and test radar → verifier → Spanish content assembly → queue.
9. Publication/analytics: validate a safe Metricool draft/review flow and analytics feedback loop. Do not publish real content until all prior gates pass and the owner approves.
10. Reporting: record timestamp, job ID, provider state, artifact URL (without temporary signed query strings), checks passed/failed, and cost tier; no secrets.

## Remaining blockers
- No ordinary content jobs have been observed entering the queue; worker polling finds an empty queue.
- Radar, source verification, and Spanish content generation are not demonstrated as connected to this renderer.
- PostgreSQL data access through the Render MCP query tool is blocked by the database's empty external IP allowlist. Do not weaken it broadly; use a safe private-network or tightly scoped authorized access method.
- Persistence across restart/redeploy has not been demonstrated end to end.
- Editorial QA and the READY_TO_PUBLISH gate are not verified end to end.
- Metricool has been inspected read-only, but no real post has been sent and automated analytics feedback is not connected.
- The PostgreSQL Free expiry date needs a continuity check before 2026-11-15.

## Current status
**Renderer: diagnostic cloud render technically verified. Metricool connections/read-only analytics: verified at connector level. Full IA 3.0 autopilot: still open.**
No real social publication should be sent until upstream intake, editorial QA, persistence, and publication controls are verified.
