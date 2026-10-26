import unittest
from unittest.mock import patch

import github_oidc_auth


class OidcAuthorizationTests(unittest.TestCase):
    def test_rejects_empty_token(self):
        self.assertFalse(github_oidc_auth.verify_editorial_audit_token(""))

    def test_rejects_non_string_token(self):
        self.assertFalse(github_oidc_auth.verify_editorial_audit_token(None))

    def test_rejects_oversized_token(self):
        self.assertFalse(github_oidc_auth.verify_editorial_audit_token("x" * 16385))

    def test_rejects_invalid_token_fail_closed(self):
        with patch.object(github_oidc_auth, "_jwks_client", side_effect=RuntimeError("no network")):
            self.assertFalse(github_oidc_auth.verify_editorial_audit_token("not-a-jwt"))


if __name__ == "__main__":
    unittest.main()

# CI trigger: exercise fail-closed OIDC tests on the open pull request.
