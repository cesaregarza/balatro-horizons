"""Certify a stopped boundary without inventing a recorded future for the parent."""

import uuid

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.engine.certification import (
    PROBE_SCOPE,
    continuation_probe_path,
    read_checkpoint,
)
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.engine.native import NativeGame
from balatro_horizons.engine.provenance import continuation_fingerprint, implementation_fingerprint
from balatro_horizons.engine.replay import ReplayDivergence, check_private
from balatro_horizons.observations.projection import HandleIssuer
from balatro_horizons.storage.journal import atomic_json, digest, locked, now


def verify_continuation_probe(store, config, eid, decision, action, *, repetitions=3):
    """Three original-state comparisons plus same-action continuation comparisons.

    The probe is evaluator evidence only. It never becomes a parent action, an
    agent history event, or a claim about the parent's unobserved future.
    """
    if type(repetitions) is not int or repetitions < 3:
        raise ValueError("AT_LEAST_THREE_REPETITIONS_REQUIRED")
    events = store.events(eid)
    if not events or events[-1]["type"] != "terminal":
        raise ValueError("PARENT_MUST_BE_TERMINAL")
    observations = [e for e in events if e["type"] == "observation"]
    if not observations or observations[-1]["observation_id"] != decision:
        raise ValueError("PROBE_REQUIRES_LAST_DECISION")
    checkpoint = read_checkpoint(store, eid, decision)
    if not checkpoint.get("continuation_hash"):
        raise ValueError("CONTINUATION_HASH_REQUIRED")
    observation = Observation.model_validate(checkpoint["observation"])
    envelope = ActionEnvelope(observation_id=decision, action=action)
    validate_action(envelope, observation)
    native = checkpoint["game"]["kind"] == "native"
    source = implementation_fingerprint()
    failures, after_hashes = [], []
    lock_path = ROOT / "private/native-worker.lock" if native else store.root / "verification.lock"
    with locked(lock_path):
        for repetition in range(repetitions):
            game = None
            try:
                game = (NativeGame(config.environment, checkpoint["game"]["seed"], calibration=True)
                        if native else FakeGame())
                game.restore(checkpoint["game"])
                game.wait_ready()
                check_private(game, checkpoint["continuation_hash"], decision)
                issuer = HandleIssuer.restore(checkpoint["issuer"])
                game.apply_public_action(envelope.action, issuer, uuid.uuid4().hex)
                game.wait_ready()
                after = continuation_fingerprint(game.observe_private())
                if after_hashes and after != after_hashes[0]:
                    raise ReplayDivergence("PROBE_CONTINUATION_DIVERGENCE", game, decision)
                after_hashes.append(after)
            except (ValueError, RuntimeError, OSError) as error:
                artifact = None
                if isinstance(error, ReplayDivergence):
                    artifact = "probe-divergence-" + uuid.uuid4().hex + ".json"
                    store.private_json(eid, artifact, {"decision": decision,
                        "actual": error.actual, "expected_start_hash": checkpoint["continuation_hash"],
                        "expected_after_hash": after_hashes[0] if after_hashes else None})
                failures.append({"repetition": repetition,
                                 "artifact": artifact,
                                 "reason": str(error) if str(error).isupper() else type(error).__name__})
            finally:
                if game is not None:
                    try:
                        game.close()
                    except (ValueError, RuntimeError, OSError):
                        failures.append({"repetition": repetition, "reason": "NATIVE_CLEANUP_FAILED"})
            if failures:
                break
    if source != implementation_fingerprint():
        failures.append({"reason": "SOURCE_CHANGED_DURING_VERIFICATION"})
    cert = {
        "schema_version": 2,
        "certificate_id": uuid.uuid4().hex,
        "episode_id": eid, "decision": decision,
        "mode": "checkpoint_probe",
        "scope": PROBE_SCOPE,
        "evidence_kind": store.manifest(eid)["evidence_kind"],
        "status": "failed" if failures else "passed",
        "repetitions": repetitions, "completed_repetitions": len(after_hashes),
        "phase": observation.phase,
        "checkpoint_hash": digest(checkpoint),
        "environment_hash": digest(checkpoint["game"].get("environment", {"kind": "synthetic"})),
        "implementation_hash": source,
        "recorded_implementation_hash": checkpoint.get("implementation_hash"),
        "probe": envelope.model_dump(mode="json"),
        "after_hashes": after_hashes,
        "created_at": now(), "failures": failures,
    }
    path = continuation_probe_path(store, eid, decision)
    atomic_json(path.with_name("certificate-record-" + cert["certificate_id"] + ".json"),
                cert, immutable=True)
    # Any failed original-state restoration disables this boundary, regardless
    # of the mode that previously selected it. Historical records are preserved.
    atomic_json(path, cert)
    return cert
