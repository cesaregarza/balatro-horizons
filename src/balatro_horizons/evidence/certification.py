"""Fresh-process replay evidence tied to native files and application source."""

import json
import uuid

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.lock import read_lock
from balatro_horizons.evidence.provenance import (
    accepted_source_matches,
    continuation_fingerprint,
    implementation_fingerprint,
)
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.replay import (
    ReplayDivergence,
    check_private,
    replay_steps,
    restore_seed_prefix,
)
from balatro_horizons.game.session import NativeFailure, NativeGame
from balatro_horizons.game.windows_context import SESSION_ERROR_CODES, load_session
from balatro_horizons.observations.projection import HandleIssuer
from balatro_horizons.storage.journal import atomic_json, digest, locked, now


def require_environment_certificate(lock, environment):
    path = ROOT / "private/capability-certificate.json"
    if not path.is_file():
        raise NativeFailure("NATIVE_CAPABILITY_CERTIFICATION_REQUIRED")
    cert = json.loads(path.read_text())
    if (
        cert.get("environment_hash") != digest(lock)
        or not accepted_source_matches(cert)
        or [environment.deck, environment.stake] not in cert.get("configurations", [])
        or cert.get("status") != "passed"
    ):
        raise NativeFailure("NATIVE_CAPABILITY_CERTIFICATE_MISMATCH")
    return cert


def certificate_path(store, eid, decision):
    return store.episode_path(eid, True) / f"certificate-{decision}.json"

def read_checkpoint(store, eid, decision):
    return json.loads((store.episode_path(eid, True) / f"checkpoint-{decision}.json").read_text())

def private_hash(store, eid, decision):
    path = store.episode_path(eid, True) / f"raw-{decision}.json"
    return continuation_fingerprint(json.loads(path.read_text())) if path.exists() else None

def steps_for(store, eid):
    events = store.events(eid)
    steps = []
    for i, event in enumerate(events):
        if event["type"] not in ("action_commit", "evaluator_fixture"):
            continue
        following = next((e for e in events[i + 1 :] if e["type"] == "observation"), None)
        if following is None:
            raise ValueError("MISSING_REPLAY_OBSERVATION")
        step = {
            "kind": "action" if event["type"] == "action_commit" else "fixture",
            "sequence": event["sequence"],
            "observation": following["payload"],
            "continuation_hash": private_hash(store, eid, following["observation_id"]),
        }
        if step["kind"] == "action":
            step["envelope"] = event["payload"]
        else:
            step["case"] = event["payload"]["case"]
        steps.append(step)
    return steps

def prefix_snapshot(store, eid, decision, steps):
    first = read_checkpoint(store, eid, 0)
    if first["game"]["kind"] != "native":
        raise ValueError("SEED_REPLAY_REQUIRES_NATIVE_EPISODE")
    if store.manifest(eid).get("parent_episode_id"):
        raise ValueError("SEED_REPLAY_REQUIRES_ROOT_EPISODE")
    return {
        "kind": "native",
        "restoration": "seed_prefix",
        "environment": first["game"]["environment"],
        "seed": first["game"]["seed"],
        "initial_issuer": first["issuer"],
        "episode_id": eid,
        "initial_continuation_hash": private_hash(store, eid, 0),
        "steps": [s for s in steps if s["observation"]["observation_id"] <= decision],
    }

def _failure_record(store, eid, decision, repetition, error):
    artifact = None
    if isinstance(error, ReplayDivergence):
        boundary = error.decision if error.decision is not None else decision
        artifact = "divergence-" + uuid.uuid4().hex + ".json"
        expected_path = store.episode_path(eid, True) / f"raw-{boundary}.json"
        atomic_json(
            store.episode_path(eid, True) / artifact,
            {
                "decision": boundary,
                "actual": error.actual,
                "expected": json.loads(expected_path.read_text())
                if expected_path.exists()
                else None,
            },
            immutable=True,
        )
    return {
        "artifact": artifact,
        "decision": getattr(error, "decision", decision),
        "repetition": repetition,
        "reason": str(error) if str(error).isupper() else type(error).__name__,
    }

def _replay_once(store, config, eid, decision, mode, checkpoint, suffix, snapshot, repetition):
    game = None
    try:
        native = checkpoint["game"]["kind"] == "native"
        game = NativeGame(config.environment, snapshot["seed"], calibration=True) if native else FakeGame()
        if mode == "seed_prefix":
            issuer = restore_seed_prefix(game, snapshot)
        else:
            game.restore(snapshot)
            issuer = HandleIssuer.restore(checkpoint["issuer"])
        check_private(game, checkpoint.get("continuation_hash") or private_hash(store, eid, decision))
        replay_steps(game, issuer, suffix, eid)
        expected_terminal = (store.summary(eid) or {}).get("outcome")
        if expected_terminal in ("WIN", "GAME_LOSS") and game.terminal_status() != expected_terminal:
            raise ValueError("TERMINAL_MISMATCH")
    except (ValueError, RuntimeError, OSError) as error:
        if native and str(error) in SESSION_ERROR_CODES:
            raise ValueError(str(error)) from None
        return _failure_record(store, eid, decision, repetition, error)
    finally:
        if game is not None:
            game.close()
    return None

def _replay_failures(store, config, eid, decision, mode, checkpoint, suffix, snapshot, repetitions):
    native = checkpoint["game"]["kind"] == "native"
    lock_path = ROOT / "private/native-worker.lock" if native else store.root / "verification.lock"
    with locked(lock_path):
        for repetition in range(repetitions):
            if native:
                load_session()
            failure = _replay_once(
                store, config, eid, decision, mode, checkpoint, suffix, snapshot, repetition
            )
            if failure:
                return [failure]
    return []

def _checkpoint_certificate(
    store, eid, decision, mode, checkpoint, suffix, snapshot, source, failures, repetitions
):
    return {
        "schema_version": 2,
        "certificate_id": uuid.uuid4().hex,
        "episode_id": eid,
        "decision": decision,
        "mode": mode,
        "evidence_kind": store.manifest(eid)["evidence_kind"],
        "status": "failed" if failures else "passed",
        "repetitions": repetitions,
        "phase": checkpoint["observation"]["phase"],
        "checkpoint_hash": digest(checkpoint),
        "environment_hash": digest(snapshot.get("environment", {"kind": "synthetic"})),
        "implementation_hash": source,
        "recorded_implementation_hash": checkpoint.get("implementation_hash"),
        "suffix_hash": digest(suffix),
        "created_at": now(),
        "failures": failures,
    }

def verify_checkpoint(store, config, eid, decision, *, repetitions=3, mode="checkpoint"):
    if type(repetitions) is not int or repetitions < 3:
        raise ValueError("AT_LEAST_THREE_REPETITIONS_REQUIRED")
    if mode not in ("checkpoint", "seed_prefix"):
        raise ValueError("UNKNOWN_RESTORATION_MODE")
    checkpoint = read_checkpoint(store, eid, decision)
    if checkpoint["game"]["kind"] == "native":
        load_session()
    steps = steps_for(store, eid)
    suffix = [s for s in steps if s["observation"]["observation_id"] > decision]
    if not any(s["kind"] == "action" for s in suffix):
        raise ValueError("NO_REPLAY_SUFFIX")
    source_hash = implementation_fingerprint()
    snapshot = (
        prefix_snapshot(store, eid, decision, steps)
        if mode == "seed_prefix"
        else checkpoint["game"]
    )
    failures = _replay_failures(
        store, config, eid, decision, mode, checkpoint, suffix, snapshot, repetitions
    )
    if source_hash != implementation_fingerprint():
        failures.append({"reason": "SOURCE_CHANGED_DURING_VERIFICATION"})
    cert = _checkpoint_certificate(
        store,
        eid,
        decision,
        mode,
        checkpoint,
        suffix,
        snapshot,
        source_hash,
        failures,
        repetitions,
    )
    path = certificate_path(store, eid, decision)
    atomic_json(
        path.with_name("certificate-record-" + cert["certificate_id"] + ".json"),
        cert,
        immutable=True,
    )
    if not failures:
        # Mutable selection points to immutable evidence; previous records are retained.
        atomic_json(path, cert)
    else:
        # A failed recheck disables the previous certificate for the same mode.
        if path.exists() and json.loads(path.read_text()).get("mode", "checkpoint") == mode:
            atomic_json(path, cert)
    return cert


def require_checkpoint_certificate(store, eid, decision):
    path = certificate_path(store, eid, decision)
    if not path.is_file():
        raise ValueError("CHECKPOINT_NOT_CERTIFIED")
    cert = json.loads(path.read_text())
    checkpoint = read_checkpoint(store, eid, decision)
    if (
        cert["status"] != "passed"
        or cert["checkpoint_hash"] != digest(checkpoint)
        or cert.get("implementation_hash") != implementation_fingerprint()
    ):
        raise ValueError("CHECKPOINT_CERTIFICATE_INVALID")
    if checkpoint["game"]["kind"] == "native":
        current = read_lock(ROOT)
        if digest(current) != cert["environment_hash"]:
            raise ValueError("CHECKPOINT_ENVIRONMENT_MISMATCH")
    if cert.get("mode") == "seed_prefix":
        checkpoint["game"] = prefix_snapshot(store, eid, decision, steps_for(store, eid))
    return checkpoint, cert


def continuation_probe(store, config, episode_id, *, decision=0, restoration="checkpoint"):
    """Require a passing replay proof; direct-save failures remain recorded and tolerated.

    ``certify_prefix`` is currently called only from this probe, so its failed
    certificate is returned here rather than treated as a successful CLI exit.
    """
    if restoration == "seed_prefix":
        from balatro_horizons.evidence.collect.prefix import certify_prefix

        certificate = certify_prefix(store, episode_id)
    elif restoration == "checkpoint":
        certificate = verify_checkpoint(
            store, config, episode_id, decision, mode="checkpoint"
        )
    else:
        raise ValueError("UNKNOWN_RESTORATION_MODE")
    if certificate["status"] != "passed" and restoration == "seed_prefix":
        raise ValueError("CONTINUATION_CERTIFICATION_FAILED")
    return certificate
