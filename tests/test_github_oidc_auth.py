import unittest
from unittest.mock import patch

from github_oidc_auth import verify_editorial_audit_token


class EditorialAuditTokenShapeTests(unittest.TestCase):
    def test_empty_token_fails_closed(self):
        self.assertFalse(verify_editorial_audit_token(""))

    def test_non_string_token_fails_closed(self):
        self.assertFalse(verify_editorial_audit_token(None))

    def test_token_with_two_segments_fails_before_jwt_lookup(self):
        with patch("github_oidc_auth._jwks_client") as jwks:
            self.assertFalse(verify_editorial_audit_token("header.payload"))
            jwks.assert_not_called()

    def test_token_with_empty_segment_fails_before_jwt_lookup(self):
        with patch("github_oidc_auth._jwks_client") as jwks:
            self.assertFalse(verify_editorial_audit_token("header..signature"))
            jwks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
