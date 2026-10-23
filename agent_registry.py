"""Read-only loader and validator for the IA 3.0 logical-agent catalog.

This module does not execute agents, call external services, mutate queue state,
publish content, or load credentials.
"""
import json
from collections import Counter
from pathlib import Path

DEFAULT_REGISTRY = Path(__file__).resolve().parent / "config" / "agents_registry.json"
EXPECTED_AGENT_COUNT = 99
EXPECTED_CELL_COUNT = 3
EXPECTED_PER_CELL = 33
ALLOWED_PERMISSIONS = {"read_approved_inputs", "emit_structured_result"}
PROHIBITED_CAPABILITIES = {
    "publish",
    "approve_qc",
    "bypass_qc",
    "read_secrets",
    "delete_jobs",
    "arbitrary_shell",
}
ALLOWED_OUTPUT_STATES = {"ok", "needs_review", "blocked", "error"}


class RegistryValidationError(ValueError):
    """Raised when the catalog is malformed or violates safety constraints."""


def validate_registry(registry):
    """Validate registry structure and safe defaults; return the same object."""
    if not isinstance(registry, dict):
        raise RegistryValidationError("registry_must_be_object")
    if registry.get("mode") != "catalog_only":
        raise RegistryValidationError("registry_must_remain_catalog_only")

    agents = registry.get("agents")
    if not isinstance(agents, list) or len(agents) != EXPECTED_AGENT_COUNT:
        raise RegistryValidationError("agent_count_must_be_99")
    if registry.get("agent_count") != EXPECTED_AGENT_COUNT:
        raise RegistryValidationError("declared_agent_count_must_be_99")
    if registry.get("cell_count") != EXPECTED_CELL_COUNT:
        raise RegistryValidationError("declared_cell_count_must_be_3")

    ids, names = [], []
    cell_counts = Counter()
    for agent in agents:
        if not isinstance(agent, dict):
            raise RegistryValidationError("agent_entry_must_be_object")
        agent_id, name, cell = agent.get("id"), agent.get("name"), agent.get("cell")
        if not all(isinstance(value, str) and value.strip() for value in (agent_id, name, cell)):
            raise RegistryValidationError("agent_identity_fields_required")
        ids.append(agent_id)
        names.append(name)
        cell_counts[cell] += 1
        if agent.get("mode") != "planned":
            raise RegistryValidationError("agents_must_remain_planned")
        if set(agent.get("permissions", [])) != ALLOWED_PERMISSIONS:
            raise RegistryValidationError("agent_permissions_out_of_policy")
        if not PROHIBITED_CAPABILITIES.issubset(set(agent.get("prohibited", []))):
            raise RegistryValidationError("critical_capability_not_prohibited")
        if set(agent.get("output_states", [])) != ALLOWED_OUTPUT_STATES:
            raise RegistryValidationError("agent_output_states_invalid")

    if len(set(ids)) != EXPECTED_AGENT_COUNT:
        raise RegistryValidationError("agent_ids_must_be_unique")
    if len(set(names)) != EXPECTED_AGENT_COUNT:
        raise RegistryValidationError("agent_names_must_be_unique")
    if len(cell_counts) != EXPECTED_CELL_COUNT or set(cell_counts.values()) != {EXPECTED_PER_CELL}:
        raise RegistryValidationError("each_of_three_cells_must_have_33_agents")
    return registry


def load_registry(path=None):
    """Load and validate the JSON catalog without activating any role."""
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY
    try:
        with registry_path.open("r", encoding="utf-8") as handle:
            registry = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryValidationError("registry_unreadable_or_invalid_json") from exc
    return validate_registry(registry)


def list_agents(cell=None, path=None):
    """Return catalog entries, optionally filtered by cell; never executes them."""
    agents = load_registry(path)["agents"]
    if cell is None:
        return list(agents)
    return [agent for agent in agents if agent["cell"] == cell]
