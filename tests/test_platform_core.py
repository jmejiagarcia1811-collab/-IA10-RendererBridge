import unittest
from platform_core import publication_gate, validate_local_render_request

class PlatformCoreTests(unittest.TestCase):
    def test_valid_local_request(self):
        req = validate_local_render_request({
            "title": "Titular",
            "local_render": {
                "headline": "Titular verificable",
                "script": "Este es un guion de prueba con ocho palabras.",
                "source": "https://example.org/noticia",
            },
        })
        self.assertEqual(req.title, "Titular")
        self.assertTrue(req.source.startswith("https://"))

    def test_reject_short_script(self):
        with self.assertRaisesRegex(ValueError, "minimum_8_words"):
            validate_local_render_request({
                "title": "Titular",
                "local_render": {"headline": "Titular", "script": "Solo cuatro palabras"},
            })

    def test_reject_non_https_source(self):
        with self.assertRaisesRegex(ValueError, "must_be_https"):
            validate_local_render_request({
                "title": "Titular",
                "local_render": {
                    "headline": "Titular",
                    "script": "Este es un guion de prueba con ocho palabras.",
                    "source": "http://example.org",
                },
            })

    def test_technical_qc_alone_never_approves(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True, editorial_approved=False,
            approval_identity=None, automatic_publish_enabled=False,
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "editorial_approval_required")

    def test_publication_fails_closed_by_default(self):
        allowed, reason = publication_gate(
            technical_qc_passed=True, editorial_approved=True,
            approval_identity="reviewer", automatic_publish_enabled=True,
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "automatic_publication_disabled_by_policy")

if __name__ == "__main__":
    unittest.main()
