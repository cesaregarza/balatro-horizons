"""Immutable interventions; only the original public prefix is inherited."""

from balatro_horizons.agents.skills import restore_knowledge
from balatro_horizons.engine.certification import require_checkpoint_certificate


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
    events = store.events(eid)
    boundary = next(
        e for e in events if e["type"] == "observation" and e["observation_id"] == decision
    )
    prefix = [e for e in events if e["sequence"] < boundary["sequence"]]
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
    }
    private = {**store.manifest(eid, True), "branch_mode": mode}
    branch_id = store.create(manifest, private)
    return branch_id, checkpoint, prefix
