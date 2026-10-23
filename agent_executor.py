"""Allowlisted, single-order executor for the IA10 bridge.

No arbitrary shell/Python execution, no publishing, and no destructive DB actions.
Orders are handled in memory and are not persisted as payloads.
"""
import json
import os
import urllib.error
import urllib.request

ALLOWED_ACTIONS = {"health", "queue_summary", "process_next"}

def execute_order(action, token):
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        return 400, {"ok": False, "error": "action_not_allowed",
                      "allowed_actions": sorted(ALLOWED_ACTIONS)}
    if not token:
        return 503, {"ok": False, "error": "bridge_token_not_configured"}

    base = os.environ.get("IA10_PUBLIC_BASE_URL", "https://ia10-renderer-bridge.onrender.com").rstrip("/")
    if action == "health":
        method, path, payload = "GET", "/health", None
    elif action == "queue_summary":
        method, path, payload = "GET", "/queue-summary", None
    else:
        method, path, payload = "POST", "/jobs/process-next", b"{}"

    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read(200_000)
            try:
                result = json.loads(raw.decode("utf-8"))
            except Exception:
                result = {"response": "non_json"}
            return response.status, {"ok": 200 <= response.status < 300,
                                     "action": action, "result": result,
                                     "temporary_order_discarded": True}
    except urllib.error.HTTPError as exc:
        # Do not echo provider bodies, headers, or credentials.
        return 502, {"ok": False, "action": action,
                     "error": "internal_action_http_error",
                     "upstream_status": exc.code,
                     "temporary_order_discarded": True}
    except Exception as exc:
        return 503, {"ok": False, "action": action,
                     "error": "internal_action_unavailable",
                     "error_type": type(exc).__name__,
                     "temporary_order_discarded": True}
