"""Explicit budget interventions, preserving the stopped root and all-attempt costs."""

import json
import math
from copy import deepcopy

from balatro_horizons.agents.budget import Spending, validate_caps
from balatro_horizons.agents.frozen import read_protocol, restore_protocol, validate_continuation
from balatro_horizons.agents.notebook import restore_notebook
from balatro_horizons.agents.skills import restore_knowledge
from balatro_horizons.agents.tool_interface import NOTEBOOK_INTERFACES, WORKING_MEMORY_INTERFACE
from balatro_horizons.agents.working_memory import restore_working_memory
from balatro_horizons.config import Config
from balatro_horizons.engine.certification import require_checkpoint_certificate
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.storage.journal import digest, locked


def prepare_budget_continuation(store, parent_id, combined_cap, *, expected_head):
    """Prepare, but do not launch, a certified continuation of a budget-stopped root.

    The single worker serializes admission. The root spending ledger becomes the
    shared campaign ledger; parent events, manifests and checkpoints never change.
    Failed attempts retain their reservations and charges. Ordinary branches are
    deliberately unaffected by this opt-in intervention.
    """
    validate_caps(combined_cap, combined_cap)
    parent = store.manifest(parent_id)
    if parent.get("parent_episode_id") or parent.get("batch_id"):
        raise ValueError("BUDGET_EXTENSION_REQUIRES_STANDALONE_ROOT")
    events = store.events(parent_id)
    if (not events or events[-1]["type"] != "terminal"
            or events[-1]["hash"] != expected_head):
        raise ValueError("BUDGET_EXTENSION_PARENT_CHANGED")
    terminal = events[-1]["payload"]
    if (terminal.get("outcome") not in ("BUDGET_EXHAUSTED", "CAMPAIGN_INTERRUPTED")
            or terminal.get("reason") not in
            ("EPISODE_COST_CAP", "CAMPAIGN_COST_CAP", "EPISODE_AND_CAMPAIGN_COST_CAP")):
        raise ValueError("PARENT_NOT_COST_EXHAUSTED")
    boundary = next(e for e in reversed(events) if e["type"] == "observation")
    decision = boundary["observation_id"]
    checkpoint, cert = require_checkpoint_certificate(store, parent_id, decision)
    if (checkpoint["public_prefix_hash"] != boundary["hash"]
            or checkpoint["committed"] != terminal["committed_actions"]):
        raise ValueError("BUDGET_EXTENSION_CHECKPOINT_MISMATCH")
    config = Config.model_validate(store.manifest(parent_id, True)["config"])
    old_cap = config.budgets.max_episode_cost_usd
    if old_cap is None or combined_cap <= old_cap:
        raise ValueError("BUDGET_EXTENSION_MUST_INCREASE_CAP")
    config.budgets.max_episode_cost_usd = combined_cap
    config.budgets.max_batch_cost_usd = combined_cap
    config.budgets.paid_calls_enabled = True
    original_protocol = read_protocol(store, checkpoint)
    extension = {
        "version": "budget-extension-v1",
        "parent_protocol": deepcopy(checkpoint["agent_protocol"]),
        "parent_terminal_hash": expected_head,
        "previous_cap_usd": old_cap,
        "combined_cap_usd": combined_cap,
        "recorded_implementation_hash": original_protocol["implementation_hash"],
        "implementation_hash": implementation_fingerprint(),
        "certificate_id": cert["certificate_id"],
        "spending_owner_episode_id": parent_id,
        "memory_boundary": "pre_decision",
    }
    resume = deepcopy(checkpoint)
    resume["budget_extension"] = extension
    protocol = restore_protocol(store, resume)
    validate_continuation(protocol, config, parent["agent"])
    restore_knowledge(store, resume)
    prefix = events[:boundary["sequence"]]
    if protocol["interface"] in NOTEBOOK_INTERFACES:
        restore_notebook(resume.get("run_notebook"), prefix, config.budgets.memory_max_characters)
        if protocol["interface"] == WORKING_MEMORY_INTERFACE:
            restore_working_memory(resume.get("working_memory"), prefix, resume["observation"])

    path = store.episode_path(parent_id, True) / "spending.json"
    with locked(path.with_suffix(".lock")):
        entries = json.loads(path.read_text())
        if not entries or any(not e["settled"] for e in entries.values()):
            raise ValueError("BUDGET_EXTENSION_UNSETTLED_SPENDING")
        costs = [e["cost"] for e in entries.values()]
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v < 0 for v in costs):
            raise ValueError("BUDGET_EXTENSION_INVALID_LEDGER")
        parent_entries = [e for e in entries.values() if e["episode_id"] == parent_id]
        if (len(parent_entries) != terminal["provider_calls"]
                or not math.isclose(sum(e["cost"] for e in parent_entries), terminal["cost_usd"],
                                    rel_tol=0, abs_tol=1e-9)):
            raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")
        prior_cost = sum(costs)
        if prior_cost >= combined_cap:
            raise ValueError("BUDGET_EXTENSION_CAP_ALREADY_SPENT")
        # Include helper calls made after the pre-decision checkpoint and failed
        # paid attempts; neither cost nor provider-call allowance is reset.
        resume["cost"], resume["calls"] = prior_cost, len(entries)
        extension["prior_cost_usd"] = prior_cost
        extension["prior_provider_calls"] = len(entries)
        extension["ledger_hash_at_admission"] = digest(entries)
    remaining = resume["observation"]["remaining_budget"]
    remaining["provider_calls"] = max(0, config.budgets.max_provider_calls - resume["calls"])
    manifest = {
        "evidence_kind": parent["evidence_kind"], "agent": parent["agent"],
        "config": config.public(), "config_hash": digest(config.model_dump()),
        "evaluation_eligible": False, "assistance": "budget_extension",
        "parent_episode_id": parent_id, "parent_decision": decision,
        "parent_event_id": boundary["event_id"], "parent_prefix_hash": boundary["hash"],
        "certificate_id": cert["certificate_id"], "budget_extension": extension,
    }
    return {
        "config": config, "manifest": manifest, "resume": resume, "prefix": prefix,
        "spending": Spending(path, combined_cap),
        "private": {**store.manifest(parent_id, True), "config": config.model_dump(),
                    "branch_mode": "budget_extension"},
    }
