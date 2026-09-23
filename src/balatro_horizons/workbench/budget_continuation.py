"""Explicit budget interventions, preserving the stopped root and all-attempt costs."""

import json
from copy import deepcopy

from balatro_horizons.config import Config
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.evidence.recovery import recovery_checkpoint
from balatro_horizons.harness.context.freeze import (
    read_protocol,
    restore_protocol,
    validate_continuation,
)
from balatro_horizons.harness.context.memory import restore_notebook, restore_working_memory
from balatro_horizons.harness.money import Spending, validate_caps
from balatro_horizons.harness.skills import restore_knowledge
from balatro_horizons.storage.journal import digest, locked
from balatro_horizons.workbench.budget_ledger import reconcile_shared_ledger


def _read_parent(store, parent_id, expected_head):
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
    return parent, events, terminal, next(e for e in reversed(events) if e["type"] == "observation")


def _restore_state(store, parent_id, parent, events, boundary, terminal, combined_cap, expected_head):
    decision = boundary["observation_id"]
    config = Config.model_validate(store.manifest(parent_id, True)["config"])
    checkpoint, recovery = recovery_checkpoint(store, config, parent_id, decision)
    if (checkpoint["public_prefix_hash"] != boundary["hash"]
            or checkpoint["committed"] != terminal["committed_actions"]):
        raise ValueError("BUDGET_EXTENSION_CHECKPOINT_MISMATCH")
    old_cap = config.budgets.max_episode_cost_usd
    root_batch_cap = config.budgets.max_batch_cost_usd
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
        "root_batch_cap_usd": root_batch_cap,
        "combined_cap_usd": combined_cap,
        "recorded_implementation_hash": original_protocol["implementation_hash"],
        "implementation_hash": implementation_fingerprint(),
        "recovery": recovery,
        "spending_owner_episode_id": parent_id,
        "memory_boundary": "pre_decision",
    }
    resume = deepcopy(checkpoint)
    resume["budget_extension"] = extension
    protocol = restore_protocol(store, resume)
    validate_continuation(protocol, config, parent["agent"])
    restore_knowledge(store, resume)
    prefix = events[:boundary["sequence"]]
    restore_notebook(resume.get("run_notebook"), prefix, config.budgets.memory_max_characters)
    restore_working_memory(resume.get("working_memory"), prefix, resume["observation"])

    return config, extension, resume, events[:boundary["sequence"]], recovery


def _admit_ledger(store, parent_id, terminal, combined_cap, resume, extension):
    path = store.episode_path(parent_id, True) / "spending.json"
    with locked(path.with_suffix(".lock")):
        entries = json.loads(path.read_text())
        prior_cost, prior_calls = reconcile_shared_ledger(store, parent_id, terminal, entries)
        if prior_cost >= combined_cap:
            raise ValueError("BUDGET_EXTENSION_CAP_ALREADY_SPENT")
        # Include helper calls made after the pre-decision checkpoint and failed
        # paid attempts; neither cost nor provider-call allowance is reset.
        resume["cost"], resume["calls"] = prior_cost, prior_calls
        extension["prior_cost_usd"] = prior_cost
        extension["prior_provider_calls"] = prior_calls
        extension["ledger_hash_at_admission"] = digest(entries)
    return path


def prepare_budget_continuation(store, parent_id, combined_cap, *, expected_head):
    """Prepare one checked restore without changing the stopped root."""
    validate_caps(combined_cap, combined_cap)
    parent, events, terminal, boundary = _read_parent(store, parent_id, expected_head)
    config, extension, resume, prefix, recovery = _restore_state(
        store, parent_id, parent, events, boundary, terminal, combined_cap, expected_head
    )
    path = _admit_ledger(store, parent_id, terminal, combined_cap, resume, extension)
    remaining = resume["observation"]["remaining_budget"]
    remaining["provider_calls"] = max(0, config.budgets.max_provider_calls - resume["calls"])
    decision = boundary["observation_id"]
    manifest = {
        "evidence_kind": parent["evidence_kind"], "agent": parent["agent"],
        "config": config.public(), "config_hash": digest(config.model_dump()),
        "evaluation_eligible": False, "assistance": "budget_extension",
        "parent_episode_id": parent_id, "parent_decision": decision,
        "parent_event_id": boundary["event_id"], "parent_prefix_hash": boundary["hash"],
        "recovery": recovery, "budget_extension": extension,
    }
    return {
        "config": config, "manifest": manifest, "resume": resume, "prefix": prefix,
        "spending": Spending(path, combined_cap),
        "private": {**store.manifest(parent_id, True), "config": config.model_dump(),
                    "branch_mode": "budget_extension"},
    }
