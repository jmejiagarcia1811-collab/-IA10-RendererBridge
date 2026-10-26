"""Strict GitHub Actions OIDC authorization for read-only editorial audit."""
import os
from functools import lru_cache

ISSUER = "https://token.actions.githubusercontent.com"
AUDIENCE = "ia10-renderer-bridge-editorial-audit"
REPOSITORY = "jmejiagarcia1811-collab/-IA10-RendererBridge"
REF = "refs/heads/main"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/editorial-backlog-audit.yml@" + REF
ALLOWED_EVENTS = {"schedule", "workflow_dispatch", "push"}


@lru_cache(maxsize=1)
def _jwks_client():
    import jwt
    return jwt.PyJWKClient(ISSUER + "/.well-known/jwks")


def verify_editorial_audit_token(token):
    """Return True only for a valid, short-lived token from the exact audit workflow."""
    if not isinstance(token, str) or not token or len(token) > 16384:
        return False
    try:
        import jwt
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=10,
            options={"require": ["exp", "iat", "iss", "aud", "repository", "ref", "workflow_ref", "event_name"]},
        )
        return (
            claims.get("repository") == REPOSITORY
            and claims.get("ref") == REF
            and claims.get("workflow_ref") == WORKFLOW_REF
            and claims.get("event_name") in ALLOWED_EVENTS
            and claims.get("sub") == "repo:" + REPOSITORY + ":ref:" + REF
        )
    except Exception:
        # Fail closed; never log tokens, claims, or validation exceptions.
        return False
