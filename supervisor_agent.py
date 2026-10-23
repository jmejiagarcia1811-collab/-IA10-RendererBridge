"""IA3 supervisor: read-only diagnostics and safe next-step report.

The supervisor validates the logical-agent catalog but does not activate its roles.
It deliberately does not publish, approve editorial items, delete production data,
read credentials from the catalog, or execute arbitrary code.
"""
from agent_executor import execute_order
from agent_registry import load_registry


def run_supervisor(token):
    # Fail closed if the static catalog is missing or violates its safety contract.
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
            "ok": False,
            "agent": "ia3-supervisor",
            "state": "blocked",
            "catalog": {"valid": False},
            "steps": [],
            "next_step": "Repair or restore the logical-agent catalog and rerun validation.",
            "safety": {
                "no_automatic_publication": True,
                "no_bulk_editorial_approval": True,
                "no_production_data_deletion": True,
            },
        }

    steps = []
    for action in ("health", "queue_summary"):
        status, result = execute_order(action, token)
        steps.append({"action": action, "http_status": status, "result": result})
        if not result.get("ok"):
            return 503, {
                "ok": False,
                "agent": "ia3-supervisor",
                "state": "blocked",
                "catalog": catalog_status,
                "steps": steps,
                "next_step": "Resolve the reported service or database connectivity issue, then rerun.",
                "safety": {
                    "no_automatic_publication": True,
                    "no_bulk_editorial_approval": True,
                    "no_production_data_deletion": True,
                },
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
        "catalog": catalog_status,
        "queue_counts": counts,
        "queue_summary": summary,
        "steps_completed": ["health", "queue_summary"],
        "next_step": next_step,
        "safety": {
            "no_automatic_publication": True,
            "no_bulk_editorial_approval": True,
            "no_production_data_deletion": True,
        },
    }
