"""Regression tests for fail-closed Shotstack credential selection."""
import os
import unittest
from unittest.mock import patch

from app import _shotstack_key_source_for_base, _shotstack_key_for_base


class ShotstackCredentialIsolationTests(unittest.TestCase):
    def test_stage_does_not_fall_back_to_generic_key(self):
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY": "legacy"}, clear=True):
            self.assertEqual(_shotstack_key_source_for_base("https://api.shotstack.io/edit/stage"), "missing")
            self.assertEqual(_shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "")

    def test_stage_uses_only_stage_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
        }, clear=True):
            self.assertEqual(_shotstack_key_source_for_base("https://api.shotstack.io/edit/stage"), "SHOTSTACK_STAGE_API_KEY")
            self.assertEqual(_shotstack_key_for_base("https://api.shotstack.io/edit/stage"), "stage-only")

    def test_production_does_not_fall_back_to_generic_or_stage_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_API_KEY": "legacy",
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
        }, clear=True):
            self.assertEqual(_shotstack_key_source_for_base("https://api.shotstack.io/edit/v1"), "missing")
            self.assertEqual(_shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "")

    def test_production_uses_only_production_key(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
            "SHOTSTACK_PRODUCTION_API_KEY": "production-only",
        }, clear=True):
            self.assertEqual(_shotstack_key_source_for_base("https://api.shotstack.io/edit/v1"), "SHOTSTACK_PRODUCTION_API_KEY")
            self.assertEqual(_shotstack_key_for_base("https://api.shotstack.io/edit/v1"), "production-only")

    def test_stage_render_endpoint_suffix_normalizes_to_stage_base(self):
        with patch.dict(os.environ, {
            "SHOTSTACK_STAGE_API_KEY": "stage-only",
            "SHOTSTACK_API_KEY": "legacy",
        }, clear=True):
            base = "https://api.shotstack.io/edit/stage/render"
            self.assertEqual(_shotstack_key_source_for_base(base), "SHOTSTACK_STAGE_API_KEY")
            self.assertEqual(_shotstack_key_for_base(base), "stage-only")

    def test_generic_key_is_never_sent_to_unknown_or_untrusted_bases(self):
        with patch.dict(os.environ, {"SHOTSTACK_API_KEY": "legacy"}, clear=True):
            for base in (
                "https://attacker.example/edit/stage",
                "https://api.shotstack.io.evil.example/edit/stage",
                "http://api.shotstack.io/edit/stage",
                "https://api.shotstack.io/edit/custom",
                "https://api.shotstack.io/edit/stage?redirect=https://attacker.example",
                "https://api.shotstack.io:443/edit/stage",
                "https://api.shotstack.io/edit/stage#fragment",
            ):
                with self.subTest(base=base):
                    self.assertEqual(_shotstack_key_source_for_base(base), "missing")
                    self.assertEqual(_shotstack_key_for_base(base), "")


if __name__ == "__main__":
    unittest.main()
