"""Second IA3 supervisor agent: coordinated diagnostics and safe next-step report.

This agent can autonomously inspect health and queue summaries. It deliberately does
not publish, approve editorial items, delete production data, or execute arbitrary code.
"""
from agent_executor import execute_order

def run_supervisor(token):
    steps = []
    for action in ("health", "queue_summary"):
        status, result = execute_order(action, token)
        steps.append({"action": action, "http_status": status, "result": result})
        if not result.get("ok"):
            return 503, {
                "ok": False,
                "agent": "ia3-supervisor",
                "state": "blocked",
                "steps": steps,
                "next_step": "Resolve the reported service or database connectivity issue, then rerun."
            }

    health = steps[0]["result"].get("result", {})
    summary = steps[1]["result"].get("result", {})
    counts = health.get("queue_counts", {}) if isinstance(health, dict) else {}
    queue_empty = not any(
        int(value or 0) > 0 for key, value in counts.items()
        if str(key).upper() in ("QUEUED", "PENDING", "READY", "RENDER_QUEUED")
    )
    editorial_count = int(counts.get("QC_PENDING_REVIEW", 0) or 0) if isinstance(counts, dict) else 0
    if editorial_count:
        next_step = "Editorial review is required; do not auto-approve."
    elif queue_empty:
        next_step = "No queued render jobs detected; check ingestion/queue eligibility."
    else:
        next_step = "Queued work exists; use the existing single-job processor and verify its result."

    return 200, {
        "ok": True,
        "agent": "ia3-supervisor",
        "state": "diagnostics_complete",
        "queue_counts": counts,
        "queue_summary": summary,
        "steps_completed": ["health", "queue_summary"],
        "next_step": next_step,
        "safety": {
            "no_automatic_publication": True,
            "no_bulk_editorial_approval": True,
            "no_production_data_deletion": True
        }
    }
