"""Immutable interventions; only the original public prefix is inherited."""

from balatro_horizons.agents.frozen import restore_protocol, validate_continuation
from balatro_horizons.agents.notebook import restore_notebook
from balatro_horizons.agents.skills import restore_knowledge
from balatro_horizons.agents.working_memory import restore_working_memory
from balatro_horizons.evidence.certification import require_checkpoint_certificate


def inherited_events(store, eid, seen=None):
    """Reconstruct only the immutable ancestry authorized by each child manifest."""
    seen = set() if seen is None else seen
    if eid in seen:
        raise ValueError("BRANCH_ANCESTRY_CYCLE")
    seen.add(eid)
    manifest = store.manifest(eid)
    parent = manifest.get("parent_episode_id")
    if parent is None:
        return []
    events = store.events(parent)
    boundary = next((event for event in events
                     if event["event_id"] == manifest.get("parent_event_id")), None)
    if (boundary is None or boundary["type"] != "observation"
            or boundary["hash"] != manifest.get("parent_prefix_hash")
            or boundary["observation_id"] != manifest.get("parent_decision")):
        raise ValueError("BRANCH_PREFIX_MISMATCH")
    return inherited_events(store, parent, seen) + events[:boundary["sequence"]]


def prepare_branch(store, config, eid, decision, mode):
    if mode not in (
        "agent_continue",
        "single_action_override",
        "short_human_sequence",
        "human_takeover",
    ):
        raise ValueError("UNKNOWN_BRANCH_MODE")
    checkpoint, cert = require_checkpoint_certificate(store, eid, decision)
    restore_knowledge(store, checkpoint)
    parent = store.manifest(eid)
    protocol = restore_protocol(store, checkpoint)
    validate_continuation(protocol, config, parent["agent"], human=mode == "human_takeover")
    events = store.events(eid)
    boundary = next(
        e for e in events if e["type"] == "observation" and e["observation_id"] == decision
    )
    prefix = [e for e in events if e["sequence"] < boundary["sequence"]]
    prefix = inherited_events(store, eid) + prefix
    restore_notebook(checkpoint.get("run_notebook"), prefix,
                     config.budgets.memory_max_characters)
    restore_working_memory(checkpoint.get("working_memory"), prefix,
                           checkpoint["observation"])
    manifest = {
        "evidence_kind": parent["evidence_kind"],
        "agent": parent["agent"],
        "config": parent["config"],
        "evaluation_eligible": False,
        "parent_episode_id": eid,
        "parent_decision": decision,
        "parent_event_id": boundary["event_id"],
        "parent_prefix_hash": boundary["hash"],
        "assistance": mode,
        "fixture": parent.get("fixture"),
        "certificate_id": cert["certificate_id"],
        "agent_protocol": checkpoint["agent_protocol"],
    }
    private = {**store.manifest(eid, True), "branch_mode": mode}
    branch_id = store.create(manifest, private)
    return branch_id, checkpoint, prefix
