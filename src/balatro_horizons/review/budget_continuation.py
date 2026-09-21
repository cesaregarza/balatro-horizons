"""Explicit budget interventions preserving the stopped root and its ledger."""

import json
import math
from copy import deepcopy

from balatro_horizons.agents.budget import Spending, validate_caps
from balatro_horizons.agents.skills import restore_knowledge
from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import require_checkpoint_certificate
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.harness.context.freeze import restore_protocol, validate_continuation
from balatro_horizons.harness.context.memory import restore_notebook, restore_working_memory
from balatro_horizons.storage.journal import digest, locked


def _matching_spend(rows, calls, cost):
    return (
        type(calls) is int
        and calls >= 0
        and type(cost) in (int, float)
        and math.isfinite(cost)
        and cost >= 0
        and len(rows) == calls
        and math.isclose(sum(row["cost"] for row in rows), cost, rel_tol=0, abs_tol=1e-9)
    )


def _validate_rows(entries):
    if not isinstance(entries, dict) or not entries:
        raise ValueError("BUDGET_EXTENSION_INVALID_LEDGER")
    for row in entries.values():
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("episode_id"), str)
            or type(row.get("settled")) is not bool
            or type(row.get("cost")) not in (int, float)
            or not math.isfinite(row["cost"])
            or row["cost"] < 0
        ):
            raise ValueError("BUDGET_EXTENSION_INVALID_LEDGER")


def _reconcile_parent(parent_id, terminal, entries):
    rows = [row for row in entries.values() if row["episode_id"] == parent_id]
    if not _matching_spend(rows, terminal["provider_calls"], terminal["cost_usd"]):
        raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")


def _ordered_children(store, parent_id, entries):
    accounted = {parent_id}
    pending = [
        item
        for item in store.list_episodes()
        if item["manifest"].get("parent_episode_id") == parent_id
        and item["manifest"].get("assistance") == "budget_extension"
    ]
    owners = {row["episode_id"] for row in entries.values()}
    while pending:
        preceding = {key: row for key, row in entries.items() if row["episode_id"] in accounted}
        prior_hash = digest(preceding)
        matches = [
            item
            for item in pending
            if item["manifest"].get("budget_extension", {}).get("ledger_hash_at_admission") == prior_hash
            and _matching_spend(
                list(preceding.values()),
                item["manifest"]["budget_extension"].get("prior_provider_calls"),
                item["manifest"]["budget_extension"].get("prior_cost_usd"),
            )
        ]
        if not matches:
            raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")
        match = next((item for item in matches if item["episode_id"] not in owners), matches[0])
        pending.remove(match)
        yield match
        accounted.add(match["episode_id"])
    if any(row["episode_id"] not in accounted for row in entries.values()):
        raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")


def _child_own_spend(child, events, prior_calls):
    started = any(event["type"] == "episode_start" for event in events)
    recovered = child.get("outcome") == "INFRASTRUCTURE_FAILURE" and child.get("reason") in (
        "PROCESS_INTERRUPTED",
        "AMBIGUOUS_ACTION_AFTER_CRASH",
    )
    calls = child["provider_calls"] if not started or recovered else child["provider_calls"] - prior_calls
    if not started and calls != 0:
        raise ValueError("BUDGET_EXTENSION_CHILD_SPEND_MISMATCH")
    return calls, child["cost_usd"]


def _reconcile_shared_ledger(store, parent_id, terminal, entries):
    _validate_rows(entries)
    _reconcile_parent(parent_id, terminal, entries)
    for item in _ordered_children(store, parent_id, entries):
        extension = item["manifest"].get("budget_extension") or {}
        if extension.get("spending_owner_episode_id") != parent_id:
            raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")
        child_id = item["episode_id"]
        child = store.summary(child_id)
        if child is None:
            raise ValueError("BUDGET_EXTENSION_CHILD_UNRESOLVED")
        child_rows = [row for row in entries.values() if row["episode_id"] == child_id]
        calls, cost = _child_own_spend(child, store.events(child_id), extension["prior_provider_calls"])
        if not _matching_spend(child_rows, calls, cost):
            raise ValueError("BUDGET_EXTENSION_CHILD_SPEND_MISMATCH")
    return sum(row["cost"] for row in entries.values()), len(entries)


def prepare_budget_continuation(store, parent_id, combined_cap, *, expected_head):
    """Prepare, but do not launch, a certified continuation child."""
    validate_caps(combined_cap, combined_cap)
    parent = store.manifest(parent_id)
    if parent.get("parent_episode_id") or parent.get("batch_id"):
        raise ValueError("BUDGET_EXTENSION_REQUIRES_STANDALONE_ROOT")
    events = store.events(parent_id)
    if not events or events[-1]["type"] != "terminal" or events[-1]["hash"] != expected_head:
        raise ValueError("BUDGET_EXTENSION_PARENT_CHANGED")
    terminal = events[-1]["payload"]
    allowed = ("EPISODE_COST_CAP", "CAMPAIGN_COST_CAP", "EPISODE_AND_CAMPAIGN_COST_CAP")
    if terminal.get("outcome") not in ("BUDGET_EXHAUSTED", "CAMPAIGN_INTERRUPTED") or terminal.get("reason") not in allowed:
        raise ValueError("PARENT_NOT_COST_EXHAUSTED")
    boundary = next(event for event in reversed(events) if event["type"] == "observation")
    checkpoint, cert = require_checkpoint_certificate(store, parent_id, boundary["observation_id"])
    if checkpoint["public_prefix_hash"] != boundary["hash"] or checkpoint["committed"] != terminal["committed_actions"]:
        raise ValueError("BUDGET_EXTENSION_CHECKPOINT_MISMATCH")
    config = Config.model_validate(store.manifest(parent_id, True)["config"])
    old_cap = config.budgets.max_episode_cost_usd
    if old_cap is None or combined_cap <= old_cap:
        raise ValueError("BUDGET_EXTENSION_MUST_INCREASE_CAP")
    config.budgets.max_episode_cost_usd = combined_cap
    config.budgets.max_batch_cost_usd = combined_cap
    config.budgets.paid_calls_enabled = True
    original_protocol = restore_protocol(store, checkpoint)
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
    validate_continuation(restore_protocol(store, resume), config, parent["agent"])
    restore_knowledge(store, resume)
    prefix = events[: boundary["sequence"]]
    restore_notebook(resume.get("run_notebook"), prefix, config.budgets.memory_max_characters)
    restore_working_memory(resume.get("working_memory"), prefix, resume["observation"])
    path = store.episode_path(parent_id, True) / "spending.json"
    with locked(path.with_suffix(".lock")):
        entries = json.loads(path.read_text())
        prior_cost, prior_calls = _reconcile_shared_ledger(store, parent_id, terminal, entries)
        if prior_cost >= combined_cap:
            raise ValueError("BUDGET_EXTENSION_CAP_ALREADY_SPENT")
        resume["cost"], resume["calls"] = prior_cost, prior_calls
        extension["prior_cost_usd"] = prior_cost
        extension["prior_provider_calls"] = prior_calls
        extension["ledger_hash_at_admission"] = digest(entries)
    resume["observation"]["remaining_budget"]["provider_calls"] = max(
        0, config.budgets.max_provider_calls - resume["calls"]
    )
    manifest = {
        "evidence_kind": parent["evidence_kind"],
        "agent": parent["agent"],
        "config": config.public(),
        "config_hash": digest(config.model_dump()),
        "evaluation_eligible": False,
        "assistance": "budget_extension",
        "parent_episode_id": parent_id,
        "parent_decision": boundary["observation_id"],
        "parent_event_id": boundary["event_id"],
        "parent_prefix_hash": boundary["hash"],
        "certificate_id": cert["certificate_id"],
        "budget_extension": extension,
    }
    return {
        "config": config,
        "manifest": manifest,
        "resume": resume,
        "prefix": prefix,
        "spending": Spending(path, combined_cap),
        "private": {**store.manifest(parent_id, True), "config": config.model_dump(), "branch_mode": "budget_extension"},
    }
