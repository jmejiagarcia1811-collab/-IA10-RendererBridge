"""Tests for safe catalog reporting in the existing supervisor."""
import unittest
from unittest.mock import patch

import supervisor_agent


class SupervisorCatalogTests(unittest.TestCase):
    @patch("supervisor_agent.execute_order")
    @patch("supervisor_agent.load_registry")
    def test_supervisor_reports_catalog_without_activating_roles(self, load_registry, execute_order):
        load_registry.return_value = {
            "mode": "catalog_only",
            "agent_count": 99,
            "cell_count": 3,
            "agents": [{"id": f"AG-{i:02d}"} for i in range(99)],
        }
        execute_order.side_effect = [
            (200, {"ok": True, "result": {"queue_counts": {"QC_PENDING_REVIEW": 4}}}),
            (200, {"ok": True, "result": {"queued": 0}}),
        ]

        status, result = supervisor_agent.run_supervisor("dummy-token")

        self.assertEqual(status, 200)
        self.assertTrue(result["catalog"]["valid"])
        self.assertEqual(result["catalog"]["agent_count"], 99)
        self.assertEqual(result["catalog"]["cell_count"], 3)
        self.assertEqual(result["next_step"], "Editorial review is required; do not auto-approve.")
        self.assertEqual(execute_order.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in execute_order.call_args_list],
            ["health", "queue_summary"],
        )

    @patch("supervisor_agent.execute_order")
    @patch("supervisor_agent.load_registry", side_effect=ValueError("invalid"))
    def test_invalid_catalog_blocks_before_service_calls(self, _load_registry, execute_order):
        status, result = supervisor_agent.run_supervisor("dummy-token")
        self.assertEqual(status, 503)
        self.assertEqual(result["state"], "blocked")
        self.assertFalse(result["catalog"]["valid"])
        execute_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
