"""Explicit budget interventions, preserving the stopped root and all-attempt costs."""

import json
import re
from copy import deepcopy

from balatro_horizons.config import Config
from balatro_horizons.cost_limits import MAX_FINITE_CAP_USD, binding_cap, increases_cap
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.evidence.compatibility import prepare_compatibility
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
    saved = read_checkpoint(store, parent_id, decision)
    original_protocol = read_protocol(store, saved)
    validate_continuation(original_protocol, config, parent["agent"])
    compatibility = prepare_compatibility(
        {saved["implementation_hash"], read_checkpoint(store, parent_id, 0)["implementation_hash"]},
        original_protocol, game_kind=saved["game"]["kind"],
    )
    if compatibility is None:
        original_protocol = restore_protocol(store, saved)
    elif compatibility["accepted_implementation_hash"] != implementation_fingerprint():
        raise ValueError("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED")
    checkpoint, recovery = recovery_checkpoint(
        store, config, parent_id, decision, compatibility=compatibility,
    )
    if (checkpoint["public_prefix_hash"] != boundary["hash"]
            or checkpoint["committed"] != terminal["committed_actions"]):
        raise ValueError("BUDGET_EXTENSION_CHECKPOINT_MISMATCH")
    old_cap = config.budgets.max_episode_cost_usd
    root_batch_cap = config.budgets.max_batch_cost_usd
    if not increases_cap(binding_cap(old_cap, root_batch_cap), combined_cap):
        raise ValueError("BUDGET_EXTENSION_MUST_INCREASE_CAP")
    config.budgets.max_episode_cost_usd = combined_cap
    config.budgets.max_batch_cost_usd = combined_cap
    config.budgets.paid_calls_enabled = True
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
    protocol = deepcopy(original_protocol)
    protocol["episode_limits"]["max_episode_cost_usd"] = combined_cap
    validate_continuation(protocol, config, parent["agent"])
    restore_knowledge(store, resume)
    prefix = events[:boundary["sequence"]]
    restore_notebook(resume.get("run_notebook"), prefix, config.budgets.memory_max_characters)
    restore_working_memory(resume.get("working_memory"), prefix, resume["observation"])

    return config, extension, resume, events[:boundary["sequence"]], recovery, compatibility


def _admit_ledger(store, parent_id, terminal, combined_cap, resume, extension):
    path = store.episode_path(parent_id, True) / "spending.json"
    with locked(path.with_suffix(".lock")):
        entries = json.loads(path.read_text())
        prior_cost, prior_calls = reconcile_shared_ledger(store, parent_id, terminal, entries)
        if combined_cap != "uncapped" and prior_cost >= combined_cap:
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
    config, extension, resume, prefix, recovery, compatibility = _restore_state(
        store, parent_id, parent, events, boundary, terminal, combined_cap, expected_head
    )
    path = _admit_ledger(store, parent_id, terminal, combined_cap, resume, extension)
    if compatibility:
        # The first replay reads the original protocol; later child checkpoints
        # carry this exact funded derivative. Bind both without rewriting either.
        funded = read_protocol(store, resume)
        funded["episode_limits"]["max_episode_cost_usd"] = combined_cap
        funded["budget_extension"] = deepcopy(extension)
        compatibility["budget_protocol_hash"] = digest(funded)
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
        "spending": Spending(path, combined_cap), "compatibility": compatibility,
        "private": {**store.manifest(parent_id, True), "config": config.model_dump(),
                    "branch_mode": "budget_extension"},
    }


def _child_heads(store, parent_id):
    heads = []
    for item in store.list_episodes():
        if item["manifest"].get("parent_episode_id") != parent_id:
            continue
        events = store.events(item["episode_id"])
        if not events:
            raise ValueError("BUDGET_EXTENSION_CHILD_UNRESOLVED")
        heads.append((item["episode_id"], events[-1]["hash"]))
    return sorted(heads)


def budget_offer(store, parent_id):
    """Bind additional funding to validated all-attempt spending and child heads."""
    events = store.events(parent_id)
    if not events:
        raise ValueError("PARENT_NOT_COST_EXHAUSTED")
    head = events[-1]["hash"]
    _, _, terminal, boundary = _read_parent(store, parent_id, head)
    path = store.episode_path(parent_id, True) / "spending.json"
    with locked(path.with_suffix(".lock")):
        entries = json.loads(path.read_text())
        spent, calls = reconcile_shared_ledger(store, parent_id, terminal, entries)
        children = _child_heads(store, parent_id)
        result = {"parent_terminal_hash": head, "accounted_usd": spent,
                  "additional_usd": 10, "new_cap_usd": spent + 10,
                  "decision": boundary["observation_id"]}
        result["plan_hash"] = digest({"offer": result, "ledger": entries, "calls": calls,
                                      "children": children, "source": implementation_fingerprint()})
    return result


def _preview_plan(service, parent_id, offer, cap):
    if cap != "uncapped" and cap > MAX_FINITE_CAP_USD:
        raise ValueError("BUDGET_INCREMENT_EXCEEDS_CAP_LIMIT")
    plan = prepare_budget_continuation(
        service.store, parent_id, cap, expected_head=offer["parent_terminal_hash"],
    )
    amount = service.validate_policy(plan["config"], plan["manifest"]["agent"])
    if amount is None:
        raise ValueError("BUDGET_EXTENSION_REQUIRES_PAID_MODEL")
    if not plan["spending"].affordability(amount)[0]:
        raise ValueError("BUDGET_EXTENSION_BELOW_RESERVATION")
    if plan["resume"]["calls"] >= plan["config"].budgets.max_provider_calls:
        raise ValueError("PROVIDER_CALL_LIMIT")
    return plan


def budget_preview(service, parent_id, *, paid_enabled):
    try:
        with service.admission():
            if not paid_enabled:
                raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
            offer = budget_offer(service.store, parent_id)
            offer.update(additional_available=True, additional_reason=None)
            try:
                plan = _preview_plan(service, parent_id, offer, offer["new_cap_usd"])
            except ValueError as error:
                if str(error) not in {
                    "BUDGET_EXTENSION_MUST_INCREASE_CAP", "EPISODE_CAP_BELOW_RESERVATION",
                    "BUDGET_EXTENSION_BELOW_RESERVATION", "BUDGET_INCREMENT_EXCEEDS_CAP_LIMIT",
                }:
                    raise
                offer.update(additional_available=False, additional_reason=str(error))
                plan = _preview_plan(service, parent_id, offer, "uncapped")
            offer["source_compatibility"] = "compatible_update" if plan["compatibility"] else "same_source"
            return {"episode_id": parent_id, "available": True, "reason": None, "plan": offer}
    except (OSError, ValueError, KeyError) as error:
        code = str(error) if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", str(error)) else "BUDGET_PLAN_UNAVAILABLE"
        return {"episode_id": parent_id, "available": False, "reason": code, "plan": None}
