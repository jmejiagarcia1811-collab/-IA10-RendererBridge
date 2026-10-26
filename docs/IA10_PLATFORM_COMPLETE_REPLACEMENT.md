# IA10 owned platform: full replacement specification

## Objective
Replace Shotstack's rendering role and build owned equivalents for the operational functions currently hosted in Render, while preserving existing data, manual editorial approval, and zero automatic publication. A repository change alone cannot replace the hosting provider: the current service still runs on Render. Full infrastructure independence requires a separately provisioned runtime, persistent object storage, database, secrets, domain/TLS and scheduler/worker.

## Target architecture
1. **Owned Studio UI** — Spanish, mobile-first creation, queue, review and audit screens.
2. **Owned API** — authenticated job intake, job status, health, source records, editorial review, approval and audit endpoints. Validate requests server-side; never trust browser-only validation.
3. **Owned render engine** — local FFmpeg/Pillow/Spanish TTS adapter, with an extension point for licensed footage and templates. No Shotstack dependency for this path.
4. **Durable asset storage** — object storage with private-by-default objects, signed short-lived links, retention policy and checksum. The current Render local filesystem is ephemeral and is not sufficient for production assets.
5. **Persistent data layer** — PostgreSQL migrations, job history, source evidence, QC results, approval identity and immutable audit events. Preserve existing records; no bulk delete/reject/archive without an explicitly reviewed migration.
6. **Worker and scheduler** — bounded queue, retries with backoff, stale-job recovery, idempotency keys, per-day limits and backlog circuit breaker.
7. **Technical QC** — decode, dimensions, duration, audio stream, audio/video sync where available, safe margins and file integrity.
8. **Editorial QC** — source link/date, claim verification, spelling, names/numbers, rights/safety checks and human sign-off. Technical QC must never auto-approve editorially.
9. **Publisher adapters** — Metricool or direct platform APIs only after credentials and account permissions are verified. Publishing remains manual and disabled by default; READY_TO_PUBLISH is not PUBLISHED.
10. **Observability and security** — redacted logs, health/metrics, rate limits, CSRF/auth protections, secret rotation, least privilege, backup/restore drills and incident audit.

## Existing facts verified on 2026-10-26
- The current Python service and PostgreSQL database are hosted on Render.
- The local narrated-card renderer can produce vertical MP4s and technical QC has passed for smoke-test jobs.
- The existing queue contains a large historical QC_PENDING_REVIEW backlog; do not auto-approve or delete it.
- Shotstack stage authentication returns HTTP 403; the production key is not configured. The local-render path must not fall back to Shotstack when a local request is valid.
- The Studio interface has been added, but its end-to-end create-to-render flow still requires production verification.
- This repository has appeared public; verify repository visibility and scan history for accidental secrets. Never paste credentials into issues, commits, logs or chat.

## Safe implementation order
### Phase A — owned video path
- Use the local_render contract and policy helpers in platform_core.py.
- Exercise authenticated POST /jobs -> QUEUED -> local worker -> technical QC -> QC_PENDING_REVIEW.
- Verify output is durable, retrievable and associated with the source and job ID.
- Prove invalid requests are rejected and local jobs never call Shotstack.
### Phase B — queue and editorial controls
- Add paginated queue filters, backlog metrics, individual approve/reject with required checklist and reason, audit events and stale-job handling.
- Reconcile historical pending items one at a time or by a reviewed migration that preserves original records and restores access.
### Phase C — owned infrastructure
- Build and test a deployable container/blueprint independent of Render; move database, media, secrets, scheduler and worker only after backup/restore validation.
- Run parallel read-only validation, then controlled cutover with rollback. Keep old hosting available until acceptance tests pass.
### Phase D — publishing and revenue
- Add provider adapters and analytics/revenue ledger after account authorization; no publishing until manual approval.
- Verify full circuit on a test account, then a human-approved real item.

## Acceptance criteria for claiming complete replacement
- Clean install/deploy from this repository without a Shotstack account.
- Durable media survives service restart/deploy.
- Authenticated job intake and queue survive restart; idempotent retries do not duplicate videos.
- All technical QC checks are recorded and inspectable.
- No video can publish without editorial approval, and default automatic publication is off.
- Audit records capture creation, QC, approval/rejection, publication attempt and result without secrets.
- Database and media backup/restore tested.
- Health, logs, error recovery and cost are measured under the approved budget.
- End-to-end acceptance tests pass on the target hosting environment.

Until all acceptance criteria pass, the status is **partial replacement**, not complete replacement.
