"""Provider-independent core policies for the IA10 owned platform."""
from dataclasses import dataclass
from typing import Any, Mapping

LOCAL_RENDERER = "ia10-local-renderer"
PENDING_REVIEW = "QC_PENDING_REVIEW"
READY_TO_PUBLISH = "READY_TO_PUBLISH"
PUBLISHED = "PUBLISHED"

@dataclass(frozen=True)
class LocalRenderRequest:
    title: str
    headline: str
    script: str
    source: str = ""

def validate_local_render_request(payload: Mapping[str, Any]) -> LocalRenderRequest:
    """Validate an owned-render request before it enters the queue."""
    title = payload.get("title", "")
    spec = payload.get("local_render")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title_required")
    if not isinstance(spec, Mapping):
        raise ValueError("local_render_required")
    headline, script, source = spec.get("headline"), spec.get("script"), spec.get("source", "")
    if not isinstance(headline, str) or not headline.strip():
        raise ValueError("local_render_headline_required")
    if len(headline.strip()) > 110:
        raise ValueError("local_render_headline_too_long")
    if not isinstance(script, str) or len(script.split()) < 8:
        raise ValueError("local_render_script_minimum_8_words")
    if len(script) > 1800:
        raise ValueError("local_render_script_too_long")
    if not isinstance(source, str) or len(source) > 500:
        raise ValueError("local_render_source_invalid")
    if source and not source.startswith("https://"):
        raise ValueError("local_render_source_must_be_https")
    return LocalRenderRequest(title.strip()[:200], headline.strip(), " ".join(script.split()), source.strip())

def publication_gate(*, technical_qc_passed: bool, editorial_approved: bool,
                     approval_identity: str | None, automatic_publish_enabled: bool = False) -> tuple[bool, str]:
    """Fail closed: technical QC is not editorial approval."""
    if automatic_publish_enabled:
        return False, "automatic_publication_disabled_by_policy"
    if not technical_qc_passed:
        return False, "technical_qc_required"
    if not editorial_approved:
        return False, "editorial_approval_required"
    if not isinstance(approval_identity, str) or not approval_identity.strip():
        return False, "approval_identity_required"
    return True, "manual_approval_gate_passed"
