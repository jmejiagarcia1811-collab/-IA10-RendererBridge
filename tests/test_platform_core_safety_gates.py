"""Regression tests for request validation and the fail-closed publication gate.

Run with: python -m unittest tests.test_platform_core_safety_gates -v
"""
import unittest

from platform_core import publication_gate, validate_local_render_request


class LocalRenderRequestTests(unittest.TestCase):
    def test_valid_request_is_normalized(self):
        request = validate_local_render_request({
            "title": "  Titular  ",
            "local_render": {
                "headline": "  Titular claro  ",
                "script": "Uno dos tres cuatro cinco seis siete ocho nueve.",
                "source": "https://example.org/fuente",
            },
        })
        self.assertEqual(request.title, "Titular")
        self.assertEqual(request.headline, "Titular claro")
        self.assertEqual(request.script, "Uno dos tres cuatro cinco seis siete ocho nueve.")
        self.assertEqual(request.source, "https://example.org/fuente")

    def test_rejects_non_https_source(self):
        with self.assertRaisesRegex(ValueError, "source_must_be_https"):
            validate_local_render_request({
                "title": "Titular",
                "local_render": {
                    "headline": "Titular claro",
                    "script": "Uno dos tres cuatro cinco seis siete ocho nueve.",
                    "source": "http://example.org/fuente",
                },
            })

    def test_rejects_script_below_minimum_word_count(self):
        with self.assertRaisesRegex(ValueError, "minimum_8_words"):
            validate_local_render_request({
                "title": "Titular",
                "local_render": {"headline": "Titular claro", "script": "Solo seis palabras en este texto"},
            })


class PublicationGateTests(unittest.TestCase):
    def test_automatic_publication_is_always_blocked(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True,
            editorial_approved=True,
            approval_identity="human-reviewer",
            automatic_publish_enabled=True,
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "automatic_publication_disabled_by_policy")

    def test_technical_qc_is_required(self):
        allowed, reason = publication_gate(
            technical_qc_passed=False,
            editorial_approved=True,
            approval_identity="human-reviewer",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "technical_qc_required")

    def test_editorial_approval_is_required(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True,
            editorial_approved=False,
            approval_identity="human-reviewer",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "editorial_approval_required")

    def test_named_human_approval_is_required(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True,
            editorial_approved=True,
            approval_identity="  ",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "approval_identity_required")

    def test_only_technically_checked_manually_approved_content_can_pass(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True,
            editorial_approved=True,
            approval_identity="human-reviewer",
            automatic_publish_enabled=False,
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "manual_approval_gate_passed")


if __name__ == "__main__":
    unittest.main()
