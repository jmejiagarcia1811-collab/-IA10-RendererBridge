"""Structural safety tests for the IA 3.0 logical-agent catalog.

Run from the repository root with:
    python -m unittest discover -s tests -v
"""
import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "agents_registry.json"


class AgentRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            cls.registry = json.load(handle)
        cls.agents = cls.registry["agents"]

    def test_catalog_is_not_live_execution(self):
        self.assertEqual(self.registry["mode"], "catalog_only")
        self.assertTrue(all(agent["mode"] == "planned" for agent in self.agents))

    def test_exactly_99_agents_in_three_cells_of_33(self):
        self.assertEqual(self.registry["agent_count"], 99)
        self.assertEqual(len(self.agents), 99)
        self.assertEqual(self.registry["cell_count"], 3)
        counts = Counter(agent["cell"] for agent in self.agents)
        self.assertEqual(len(counts), 3)
        self.assertEqual(set(counts.values()), {33})

    def test_agent_ids_and_names_are_unique(self):
        ids = [agent["id"] for agent in self.agents]
        names = [agent["name"] for agent in self.agents]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(names), len(set(names)))

    def test_roles_have_only_minimal_permissions(self):
        allowed = {"read_approved_inputs", "emit_structured_result"}
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertEqual(set(agent["permissions"]), allowed)

    def test_critical_actions_are_prohibited_for_every_role(self):
        required = {
            "publish",
            "approve_qc",
            "bypass_qc",
            "read_secrets",
            "delete_jobs",
            "arbitrary_shell",
        }
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertTrue(required.issubset(set(agent["prohibited"])))

    def test_output_states_are_known(self):
        allowed_states = {"ok", "needs_review", "blocked", "error"}
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertEqual(set(agent["output_states"]), allowed_states)


if __name__ == "__main__":
    unittest.main()
