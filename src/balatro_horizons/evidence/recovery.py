"""Read-only recovery admission; the worker verifies one restore before playing."""

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.certification import (
    private_hash,
    read_checkpoint,
    require_environment_certificate,
    steps_for,
)
from balatro_horizons.evidence.lock import read_lock
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.storage.journal import digest


def recovery_checkpoint(store, config, eid, decision):
    """Prepare a journal-bound restore, not a claim that a replay already passed."""
    checkpoint, _ = _bound_checkpoint(store, eid, decision)
    receipt = {"policy": "single_restore_v1", "checkpoint_hash": digest(checkpoint),
               "mode": "checkpoint"}
    if checkpoint["game"]["kind"] == "native":
        lock = read_lock(ROOT)
        if checkpoint["game"]["environment"] != lock:
            raise ValueError("CHECKPOINT_ENVIRONMENT_MISMATCH")
        try:
            require_environment_certificate(lock, config.environment)
        except NativeFailure as error:
            raise ValueError(error.code) from None
        checkpoint["game"] = _native_prefix(store, eid, decision, set())
        receipt["mode"] = "seed_prefix"
    elif checkpoint["game"]["kind"] != "synthetic":
        raise ValueError("CHECKPOINT_KIND_UNSUPPORTED")
    return checkpoint, receipt


def _bound_checkpoint(store, eid, decision):
    events = store.events(eid)  # Validates the immutable journal's hash chain.
    boundary = next((e for e in events if e["type"] == "observation"
                     and e["observation_id"] == decision), None)
    checkpoint = read_checkpoint(store, eid, decision)
    if (boundary is None or checkpoint.get("public_prefix_hash") != boundary["hash"]
            or checkpoint.get("observation") != boundary["payload"]):
        raise ValueError("CHECKPOINT_PREFIX_MISMATCH")
    if checkpoint.get("implementation_hash") != implementation_fingerprint():
        raise ValueError("CHECKPOINT_IMPLEMENTATION_CHANGED")
    expected = checkpoint.get("continuation_hash")
    if not expected or expected != private_hash(store, eid, decision):
        raise ValueError("CHECKPOINT_CONTINUATION_MISMATCH")
    return checkpoint, boundary


def _native_prefix(store, eid, decision, seen):
    """Replay only the selected ancestry, never a parent's discarded future."""
    if eid in seen:
        raise ValueError("BRANCH_ANCESTRY_CYCLE")
    seen.add(eid)
    checkpoint, _ = _bound_checkpoint(store, eid, decision)
    manifest = store.manifest(eid)
    if manifest.get("fixture"):
        raise ValueError("RECOVERY_FIXTURE_NOT_SUPPORTED")
    parent = manifest.get("parent_episode_id")
    if parent is not None:
        _, boundary = _bound_checkpoint(store, parent, manifest["parent_decision"])
        if (boundary["event_id"] != manifest.get("parent_event_id")
                or boundary["hash"] != manifest.get("parent_prefix_hash")
                or decision < manifest["parent_decision"]):
            raise ValueError("BRANCH_PREFIX_MISMATCH")
        snapshot = _native_prefix(store, parent, manifest["parent_decision"], seen)
    else:
        first, _ = _bound_checkpoint(store, eid, 0)
        snapshot = {
            "kind": "native", "restoration": "seed_prefix",
            "environment": first["game"]["environment"], "seed": first["game"]["seed"],
            "initial_issuer": first["issuer"], "episode_id": eid,
            "initial_continuation_hash": first["continuation_hash"], "steps": [],
        }
    if (checkpoint["game"].get("environment") != snapshot["environment"]
            or checkpoint["game"].get("seed") != snapshot["seed"]):
        raise ValueError("CHECKPOINT_ENVIRONMENT_MISMATCH")
    steps = steps_for(store, eid, through=decision)
    if any(not step["continuation_hash"] for step in steps):
        raise ValueError("CHECKPOINT_CONTINUATION_MISMATCH")
    if any(step["kind"] != "action" for step in steps):
        raise ValueError("RECOVERY_FIXTURE_NOT_SUPPORTED")
    snapshot["steps"].extend(steps)
    if snapshot["steps"]:
        last = snapshot["steps"][-1]
        if (last["observation"]["public_state_hash"] != checkpoint["observation"]["public_state_hash"]
                or last["continuation_hash"] != checkpoint["continuation_hash"]):
            raise ValueError("CHECKPOINT_PREFIX_MISMATCH")
    elif checkpoint["continuation_hash"] != snapshot["initial_continuation_hash"]:
        raise ValueError("CHECKPOINT_PREFIX_MISMATCH")
    return snapshot
