"""Structural and safety tests for the IA 3.0 logical-agent catalog."""
import copy
import unittest
from collections import Counter

from agent_registry import (
    RegistryValidationError,
    list_agents,
    load_registry,
    validate_registry,
)


class AgentRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.agents = cls.registry["agents"]

    def test_catalog_is_not_live_execution(self):
        self.assertEqual(self.registry["mode"], "catalog_only")
        self.assertTrue(all(agent["mode"] == "planned" for agent in self.agents))

    def test_exactly_99_agents_in_three_cells_of_33(self):
        self.assertEqual(len(self.agents), 99)
        counts = Counter(agent["cell"] for agent in self.agents)
        self.assertEqual(len(counts), 3)
        self.assertEqual(set(counts.values()), {33})

    def test_agent_ids_and_names_are_unique(self):
        self.assertEqual(len({a["id"] for a in self.agents}), 99)
        self.assertEqual(len({a["name"] for a in self.agents}), 99)

    def test_roles_have_only_minimal_permissions(self):
        allowed = {"read_approved_inputs", "emit_structured_result"}
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertEqual(set(agent["permissions"]), allowed)

    def test_critical_actions_are_prohibited_for_every_role(self):
        required = {
            "publish", "approve_qc", "bypass_qc",
            "read_secrets", "delete_jobs", "arbitrary_shell",
        }
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertTrue(required.issubset(set(agent["prohibited"])))

    def test_output_states_are_known(self):
        allowed = {"ok", "needs_review", "blocked", "error"}
        for agent in self.agents:
            with self.subTest(agent=agent["id"]):
                self.assertEqual(set(agent["output_states"]), allowed)

    def test_read_only_filter_returns_one_cell(self):
        cell = self.agents[0]["cell"]
        selected = list_agents(cell)
        self.assertEqual(len(selected), 33)
        self.assertTrue(all(agent["cell"] == cell for agent in selected))

    def test_validator_rejects_catalog_activation(self):
        altered = copy.deepcopy(self.registry)
        altered["mode"] = "active"
        with self.assertRaises(RegistryValidationError):
            validate_registry(altered)

    def test_validator_rejects_permission_escalation(self):
        altered = copy.deepcopy(self.registry)
        altered["agents"][0]["permissions"].append("publish")
        with self.assertRaises(RegistryValidationError):
            validate_registry(altered)


if __name__ == "__main__":
    unittest.main()
