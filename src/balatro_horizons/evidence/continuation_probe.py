"""Same-action continuation evidence for an explicitly stopped episode."""

import re
import uuid

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evidence.certification import (
    PROBE_SCOPE,
    continuation_probe_path,
    read_checkpoint,
)
from balatro_horizons.evidence.provenance import (
    continuation_fingerprint,
    implementation_fingerprint,
)
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.replay import ReplayDivergence, check_private
from balatro_horizons.game.session import NativeGame
from balatro_horizons.game.windows_context import load_session
from balatro_horizons.observations.projection import HandleIssuer
from balatro_horizons.storage.journal import atomic_json, digest, locked, now


def _probe_inputs(store, config, eid, decision, action, repetitions):
    if type(repetitions) is not int or repetitions < 3:
        raise ValueError("AT_LEAST_THREE_REPETITIONS_REQUIRED")
    checkpoint = read_checkpoint(store, eid, decision)
    native = checkpoint["game"]["kind"] == "native"
    if native:
        # Check the explicit Windows registration before inspecting the parent
        # journal or validating the generated action.
        load_session()
    events = store.events(eid)
    if not events or events[-1]["type"] != "terminal":
        raise ValueError("PARENT_MUST_BE_TERMINAL")
    observations = [event for event in events if event["type"] == "observation"]
    if not observations or observations[-1]["observation_id"] != decision:
        raise ValueError("PROBE_REQUIRES_LAST_DECISION")
    if not checkpoint.get("continuation_hash"):
        raise ValueError("CONTINUATION_HASH_REQUIRED")
    observation = Observation.model_validate(checkpoint["observation"])
    envelope = ActionEnvelope(observation_id=decision, action=action)
    validate_action(envelope, observation)
    return checkpoint, envelope, native


def _new_game(config, checkpoint, native):
    if native:
        return NativeGame(config.environment, checkpoint["game"]["seed"], calibration=True)
    return FakeGame()


def _probe_game(game, checkpoint, envelope, decision, after_hashes):
    game.restore(checkpoint["game"])
    game.wait_ready()
    check_private(game, checkpoint["continuation_hash"], decision)
    issuer = HandleIssuer.restore(checkpoint["issuer"])
    game.apply_public_action(envelope.action, issuer, uuid.uuid4().hex)
    game.wait_ready()
    after = continuation_fingerprint(game.observe_private())
    if after_hashes and after != after_hashes[0]:
        raise ReplayDivergence("PROBE_CONTINUATION_DIVERGENCE", game, decision)
    return after


def _record_divergence(store, eid, decision, error, repetition, checkpoint, after_hashes):
    artifact = "probe-divergence-" + uuid.uuid4().hex + ".json"
    store.private_json(
        eid,
        artifact,
        {
            "decision": decision,
            "actual": error.actual,
            "expected_start_hash": checkpoint["continuation_hash"],
            "expected_after_hash": after_hashes[0] if after_hashes else None,
        },
    )
    return {
        "repetition": repetition,
        "artifact": artifact,
        "reason": str(error) if str(error).isupper() else type(error).__name__,
    }


def _close_game(game, failures, primary=None):
    if game is None:
        return
    try:
        game.close()
    except (ValueError, RuntimeError, OSError) as error:
        if primary is not None:
            # Keep the actionable primary code; notes also feed the collector's receipt.
            code = str(error) if re.fullmatch(r"[A-Z][A-Z0-9_]{0,98}", str(error)) else "NATIVE_CLEANUP_FAILED"
            primary.add_note("NATIVE_CLEANUP_FAILED: " + code)
        elif failures:
            failures.append({"repetition": failures[-1]["repetition"], "reason": "NATIVE_CLEANUP_FAILED"})
        elif isinstance(error, NativeFailure):
            raise
        else:
            raise NativeFailure("NATIVE_CLEANUP_FAILED") from error


def _run_repetitions(store, config, eid, decision, checkpoint, envelope, native, repetitions):
    failures, after_hashes = [], []
    lock_path = ROOT / "private/native-worker.lock" if native else store.root / "verification.lock"
    with locked(lock_path):
        for repetition in range(repetitions):
            if native:
                # Recheck immediately before every fresh-process comparison.
                load_session()
            game = None
            primary = None
            try:
                game = _new_game(config, checkpoint, native)
                after_hashes.append(
                    _probe_game(game, checkpoint, envelope, decision, after_hashes)
                )
            except ReplayDivergence as error:
                failures.append(
                    _record_divergence(
                        store, eid, decision, error, repetition, checkpoint, after_hashes
                    )
                )
            except (ValueError, RuntimeError, OSError) as error:
                primary = error
                raise
            finally:
                _close_game(game, failures, primary)
            if failures:
                break
    return failures, after_hashes


def _certificate(store, eid, decision, checkpoint, envelope, source, failures, after_hashes, repetitions):
    return {
        "schema_version": 2,
        "certificate_id": uuid.uuid4().hex,
        "episode_id": eid,
        "decision": decision,
        "mode": "checkpoint_probe",
        "scope": PROBE_SCOPE,
        "evidence_kind": store.manifest(eid)["evidence_kind"],
        "status": "failed" if failures else "passed",
        "repetitions": repetitions,
        "completed_repetitions": len(after_hashes),
        "phase": checkpoint["observation"]["phase"],
        "checkpoint_hash": digest(checkpoint),
        "environment_hash": digest(checkpoint["game"].get("environment", {"kind": "synthetic"})),
        "implementation_hash": source,
        "recorded_implementation_hash": checkpoint.get("implementation_hash"),
        "probe": envelope.model_dump(mode="json"),
        "after_hashes": after_hashes,
        "created_at": now(),
        "failures": failures,
    }


def verify_continuation_probe(store, config, eid, decision, action, *, repetitions=3):
    """Compare fresh restorations and one identical generated action.

    Probe evidence is evaluator-only: it never becomes a parent action, agent
    history event, or claim about the parent's unobserved future.
    """
    checkpoint, envelope, native = _probe_inputs(
        store, config, eid, decision, action, repetitions
    )
    source = implementation_fingerprint()
    failures, after_hashes = _run_repetitions(
        store, config, eid, decision, checkpoint, envelope, native, repetitions
    )
    if source != implementation_fingerprint():
        raise ValueError("SOURCE_CHANGED_DURING_VERIFICATION")
    cert = _certificate(
        store, eid, decision, checkpoint, envelope, source, failures, after_hashes, repetitions
    )
    path = continuation_probe_path(store, eid, decision)
    atomic_json(
        path.with_name("certificate-record-" + cert["certificate_id"] + ".json"),
        cert,
        immutable=True,
    )
    # Operational failures raise before this point and leave the selected probe
    # untouched. A proven divergence deliberately invalidates that pointer.
    atomic_json(path, cert)
    return cert
