"""Plan a latest-boundary restoration without spending, launching, or mutating."""

from copy import deepcopy

from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.evidence.compatibility import prepare_compatibility
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.evidence.recovery import recovery_checkpoint
from balatro_horizons.harness.context.freeze import read_protocol, validate_continuation
from balatro_horizons.harness.context.memory import restore_notebook, restore_working_memory
from balatro_horizons.harness.money import can_afford, validate_paid_configuration
from balatro_horizons.harness.skills import restore_knowledge
from balatro_horizons.harness.terminals import GAME_TERMINAL_OUTCOMES
from balatro_horizons.storage.journal import digest
from balatro_horizons.workbench.branches import inherited_events
from balatro_horizons.workbench.restore_ledger import restore_spending


def _latest_boundary(store, eid):
    parent, events = store.manifest(eid), store.events(eid)
    if parent.get("fixture"):
        raise ValueError("RECOVERY_FIXTURE_NOT_SUPPORTED")
    terminal = events[-1]["payload"] if events and events[-1]["type"] == "terminal" else {}
    if terminal.get("outcome") in GAME_TERMINAL_OUTCOMES:
        raise ValueError("RESTORE_RUN_FINISHED")
    boundary = next((event for event in reversed(events) if event["type"] == "observation"), None)
    if boundary is None:
        raise ValueError("RESTORE_CHECKPOINT_MISSING")
    tail = events[boundary["sequence"] + 1:]
    intents = {event["request_id"] for event in tail if event["type"] == "action_intent"}
    rejected = {event["request_id"] for event in tail if event["type"] == "action_rejected"}
    if intents - rejected or any(event["type"] == "action_commit" for event in tail):
        raise ValueError("RESTORE_UNSETTLED_ACTION")
    return parent, events, boundary


def _source_hashes(store, eid, checkpoint):
    sources, seen = {checkpoint["implementation_hash"]}, set()
    while True:
        if eid in seen:
            raise ValueError("BRANCH_ANCESTRY_CYCLE")
        seen.add(eid)
        manifest = store.manifest(eid)
        parent = manifest.get("parent_episode_id")
        if parent is None:
            sources.add(read_checkpoint(store, eid, 0)["implementation_hash"])
            return sources
        checkpoint = read_checkpoint(store, parent, manifest["parent_decision"])
        sources.add(checkpoint["implementation_hash"])
        eid = parent


def _budget(config, agent, ledger, checkpoint, *, paid_enabled):
    if ledger["cost"] + 1e-9 < checkpoint["cost"] or ledger["calls"] < checkpoint["calls"]:
        raise ValueError("RESTORE_LEDGER_MISMATCH")
    if checkpoint["committed"] >= config.budgets.max_game_actions:
        raise ValueError("GAME_ACTION_LIMIT")
    if ledger["calls"] >= config.budgets.max_provider_calls:
        raise ValueError("PROVIDER_CALL_LIMIT")
    paid = agent not in ("heuristic", "random_legal", "human")
    if not paid:
        return False
    if not paid_enabled:
        raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
    amount = validate_paid_configuration(config.models[agent], config.budgets)
    if not can_afford(ledger["cost"], amount, config.budgets.max_episode_cost_usd):
        raise ValueError("EPISODE_COST_CAP")
    if not can_afford(ledger["cost"], amount, config.budgets.max_batch_cost_usd):
        raise ValueError("CAMPAIGN_COST_CAP")
    return True


def prepare_restore(store, eid, *, paid_enabled):
    parent, events, boundary = _latest_boundary(store, eid)
    ledger = restore_spending(store, eid)
    config = Config.model_validate(store.manifest(eid, True)["config"])
    checkpoint = read_checkpoint(store, eid, boundary["observation_id"])
    protocol = read_protocol(store, checkpoint)
    validate_continuation(protocol, config, parent["agent"])
    knowledge = restore_knowledge(store, checkpoint)
    if digest(knowledge) != protocol["knowledge_hash"]:
        raise ValueError("AGENT_PROTOCOL_KNOWLEDGE_CHANGED")
    compatibility = prepare_compatibility(
        _source_hashes(store, eid, checkpoint), protocol, game_kind=checkpoint["game"]["kind"],
    )
    resume, recovery = recovery_checkpoint(
        store, config, eid, boundary["observation_id"], compatibility=compatibility,
    )
    prefix = inherited_events(store, eid) + events[:boundary["sequence"]]
    restore_notebook(resume.get("run_notebook"), prefix, config.budgets.memory_max_characters)
    restore_working_memory(resume.get("working_memory"), prefix, resume["observation"])
    paid = _budget(config, parent["agent"], ledger, checkpoint, paid_enabled=paid_enabled)
    resume["cost"], resume["calls"] = ledger["cost"], ledger["calls"]
    resume["observation"]["remaining_budget"]["provider_calls"] = max(
        0, config.budgets.max_provider_calls - ledger["calls"]
    )
    public = _public_plan(events[-1]["hash"], boundary, config, ledger, paid, compatibility, recovery)
    return {"public": public, "config": config, "parent": parent, "boundary": boundary,
            "resume": resume, "prefix": prefix, "ledger": ledger,
            "compatibility": compatibility, "recovery": recovery}


def _public_plan(head, boundary, config, ledger, paid, compatibility, recovery):
    episode, batch = config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd
    result = {
        "parent_head": head, "decision": boundary["observation_id"],
        "requires_paid_authorization": paid,
        "source_compatibility": "compatible_update" if compatibility else "same_source",
        "costs": {"accounted_usd": ledger["cost"],
                  "remaining_episode_usd": max(0, episode - ledger["cost"]) if episode else None,
                  "remaining_batch_usd": max(0, batch - ledger["cost"]) if batch else None},
        "limits": {"max_episode_cost_usd": episode, "max_batch_cost_usd": batch},
        "launches": {"verification": 0, "continuation": 1},
    }
    result["plan_hash"] = digest({"preview": result, "source": implementation_fingerprint(),
                                  "checkpoint": recovery["checkpoint_hash"],
                                  "ledger": ledger["hash"], "heads": ledger["heads"]})
    return result


def create_restoration(store, parent_id, plan):
    parent, boundary, public = plan["parent"], plan["boundary"], plan["public"]
    metadata = {"version": "run-restoration-v1", "parent_head": public["parent_head"],
                "spending_owner_episode_id": plan["ledger"]["root"],
                "prior_cost_usd": plan["ledger"]["cost"],
                "prior_provider_calls": plan["ledger"]["calls"],
                "plan_hash": public["plan_hash"],
                "source_revisions": sorted(set(
                    (plan["compatibility"] or {}).get("source_revisions", {}).values()
                )),
                "source_compatibility": public["source_compatibility"]}
    manifest = {
        "evidence_kind": parent["evidence_kind"], "agent": parent["agent"],
        "config": plan["config"].public(), "evaluation_eligible": False,
        "parent_episode_id": parent_id, "parent_decision": boundary["observation_id"],
        "parent_event_id": boundary["event_id"], "parent_prefix_hash": boundary["hash"],
        "assistance": "restoration", "restoration": metadata, "recovery": plan["recovery"],
        "agent_protocol": deepcopy(plan["resume"]["agent_protocol"]),
    }
    private = {**store.manifest(parent_id, True), "branch_mode": "restoration"}
    eid = store.create(manifest, private)
    if plan["compatibility"]:
        store.private_json(eid, "source-compatibility.json", plan["compatibility"])
        plan["resume"]["source_compatibility"] = {
            "episode_id": eid, "hash": digest(plan["compatibility"]),
        }
    return eid
