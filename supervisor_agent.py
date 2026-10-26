"""IA3 supervisor: read-only diagnosis plus one guarded repair action.

The supervisor may process at most one already-queued job when editorial backlog is
below the configured safety threshold. It never approves content, publishes, deletes
production data, changes credentials, or executes arbitrary code.
"""
from agent_executor import execute_order
from agent_registry import load_registry

EDITORIAL_BACKLOG_LIMIT = 25


def _safe_count(mapping, *keys):
    if not isinstance(mapping, dict):
        return 0
    for key in keys:
        try:
            return max(0, int(mapping.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
    return 0


def run_supervisor(token):
    safety = {
        "no_automatic_publication": True,
        "no_bulk_editorial_approval": True,
        "no_production_data_deletion": True,
        "no_secret_changes": True,
        "max_jobs_processed_per_run": 1,
    }
    try:
        catalog = load_registry()
        catalog_status = {
            "valid": True,
            "mode": catalog["mode"],
            "agent_count": len(catalog["agents"]),
            "cell_count": catalog["cell_count"],
        }
    except Exception:
        return 503, {
            "ok": False, "agent": "ia3-supervisor", "state": "blocked",
            "catalog": {"valid": False}, "steps": [],
            "next_step": "Restore and validate the logical-agent catalog.",
            "safety": safety,
        }

    steps = []
    status, health_result = execute_order("health", token)
    steps.append({"action": "health", "http_status": status, "result": health_result})
    if not health_result.get("ok"):
        return 503, {
            "ok": False, "agent": "ia3-supervisor", "state": "blocked",
            "catalog": catalog_status, "steps": steps,
            "next_step": "Health check failed; do not mutate the queue until service health is restored.",
            "safety": safety,
        }

    status, summary_result = execute_order("queue_summary", token)
    steps.append({"action": "queue_summary", "http_status": status, "result": summary_result})
    if not summary_result.get("ok"):
        return 503, {
            "ok": False, "agent": "ia3-supervisor", "state": "blocked",
            "catalog": catalog_status, "steps": steps,
            "next_step": "Queue diagnostics failed; no repair action was attempted.",
            "safety": safety,
        }

    health = health_result.get("result", {})
    counts = health.get("queue_counts", {}) if isinstance(health, dict) else {}
    queued = _safe_count(counts, "QUEUED", "queued", "PENDING", "READY", "RENDER_QUEUED")
    editorial_count = _safe_count(counts, "QC_PENDING_REVIEW", "qc_pending_review")
    steps_completed = ["health", "queue_summary"]
    action_state = "diagnostics_complete"

    # The review backlog is a human decision gate, not a technical failure to bypass.
    if editorial_count >= EDITORIAL_BACKLOG_LIMIT:
        next_step = "Blocked by editorial review backlog; human review is required. No queue mutation performed."
        action_state = "blocked_by_editorial_gate"
    elif queued > 0:
        status, process_result = execute_order("process_next", token)
        steps.append({"action": "process_next", "http_status": status, "result": process_result})
        steps_completed.append("single_job_processing_attempt")
        action_state = "single_job_attempted" if process_result.get("ok") else "repair_blocked"
        next_step = (
            "One queued job was processed; verify its persisted state and technical QC before any editorial approval."
            if process_result.get("ok") else
            "Automatic attempt did not complete. Inspect the returned safe error classification; credentials and publication remain untouched."
        )
    else:
        next_step = "No eligible queued work. Diagnose ingestion/provider blockers; do not create duplicate jobs or bypass editorial review."

    return 200, {
        "ok": action_state != "repair_blocked",
        "agent": "ia3-supervisor",
        "state": action_state,
        "catalog": catalog_status,
        "queue_counts": counts,
        "queue_summary": summary_result.get("result", {}),
        "steps_completed": steps_completed,
        "next_step": next_step,
        "safety": safety,
    }
