# IA 3.0 editorial review gate (draft)

## Scope and safety

This change adds an authenticated endpoint that records a human editorial decision for a rendered job after technical QC. It does **not** publish content, change the job's technical state, call Metricool, or enable automatic publication. The diagnostic bootstrap video is explicitly ineligible.

The branch is intentionally not deployed. Review and test it before merging or deploying.

## Endpoint

`POST /jobs/editorial-review`

Requires the existing `Authorization: Bearer <IA10_BRIDGE_TOKEN>` header and a JSON body:

```json
{
  "job_id": 123,
  "decision": "APPROVED",
  "source_urls": ["https://example.org/source"],
  "notes": "Checked names, dates, claims, captions, audio and safe zones.",
  "human_review_confirmed": true
}
```

Requirements:
- `decision` is `APPROVED` or `REJECTED`.
- `human_review_confirmed` must be true.
- 1–10 HTTPS source URLs are required.
- Review notes are required (up to 4,000 characters).
- The job must exist, must not be a diagnostic bootstrap job, and must already be `QC_PENDING_REVIEW`.
- One review record per job is stored; a later review overwrites the previous decision.
- Response explicitly states `published: false`.

## Verification plan before deployment

1. Run `python -m py_compile app.py`.
2. Test missing auth returns 401.
3. Test invalid decision, missing confirmation, empty/non-HTTPS sources, and empty notes are rejected.
4. Test missing job returns 404.
5. Test a diagnostic bootstrap job is rejected.
6. Test a job not in `QC_PENDING_REVIEW` is rejected.
7. Test a technically QC-passed non-diagnostic job can record APPROVED and REJECTED decisions.
8. Confirm logs contain no auth token, API key, source query tokens, or signed render URLs.
9. Confirm no Metricool post is created by this endpoint.
10. Deploy only after review, then run tests against a non-production test job.

## Remaining independent work

The Metricool publishing connector and media handoff are not implemented by this change. Do not claim end-to-end automation is complete until a reviewed video can be submitted to Metricool in its review/approval flow and that flow is verified without publishing a real post.
