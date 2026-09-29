"""One owned unpaid process checks every standard deck/stake start; no publication."""

import fcntl
import uuid
from contextlib import ExitStack, contextmanager

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import reuse
from balatro_horizons.evidence.configurations import CHECKS, KIND, catalog_hash, pairs
from balatro_horizons.evidence.lock import lock_digest, native_path
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.evidence.validation import require
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.session import NativeSession
from balatro_horizons.game.windows_context import SESSION_ERROR_CODES, load_session
from balatro_horizons.storage.journal import atomic_json, digest, now
from balatro_horizons.storage.private_files import _check_destination

COLLECTION_ERRORS = SESSION_ERROR_CODES | {
    "NATIVE_WORKER_BUSY", "NATIVE_CONFIGURATION_MISMATCH", "CONFIGURATION_START_NOT_READY",
    "CONFIGURATION_ENVIRONMENT_CHANGED", "CONFIGURATION_PUBLIC_OBSERVATION_INVALID",
    "SOURCE_CHANGED_DURING_COLLECTION", "ACTIVE_CERTIFICATE_CHANGED", "CERTIFIED_PROFILE_CHANGED",
}


@contextmanager
def worker_guards(root, baseline_root):
    # The baseline may still serve the dashboard. Refuse a running worker and
    # hold both admissions throughout collection; never stop someone else's run.
    with ExitStack() as stack:
        for path in sorted({root / "private/native-worker.lock", baseline_root / "private/native-worker.lock"}):
            _check_destination(path)
            stream = stack.enter_context(path.open("a"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("NATIVE_WORKER_BUSY") from None
        yield


def sample(game, deck, stake, environment_hash):
    raw = game.inspect_raw()
    require((raw.get("deck"), raw.get("stake")) == (deck, stake), "NATIVE_CONFIGURATION_MISMATCH")
    require(raw.get("state") == "BLIND_SELECT" and raw["bh"]["ready"] and not raw["bh"]["busy"],
            "CONFIGURATION_START_NOT_READY")
    require(digest(game.lock) == environment_hash, "CONFIGURATION_ENVIRONMENT_CHANGED")
    visible = game.observe_private()["visible"]
    require(visible["phase"] == "BLIND_SELECT" and "select_blind" in visible["available_action_types"],
            "CONFIGURATION_PUBLIC_OBSERVATION_INVALID")
    return {"deck": deck, "stake": stake, "observed_deck": raw["deck"], "observed_stake": raw["stake"],
            "phase": raw["state"], "profile_hash": digest(raw["bh"]["profile"]), "checks": CHECKS}


def collect(config, baseline_root, offline_report, report_path, *, session_factory=NativeSession,
            on_progress=None):
    root, baseline_root, report_path = native_path(ROOT), native_path(baseline_root), native_path(report_path)
    require(not report_path.exists(), "CONFIGURATION_REPORT_ALREADY_EXISTS")
    certificate, _ = reuse.prepare(baseline_root, root, "auto", offline_report)
    require(lock_digest(root) == certificate["environment_hash"], "CONFIGURATION_ENVIRONMENT_CHANGED")
    source = implementation_fingerprint()
    require(source == certificate["accepted_implementation_hash"], "CONFIGURATION_SOURCE_MISMATCH")
    report = {
        "schema_version": 1, "kind": KIND, "status": "failed", "created_at": now(),
        "implementation_hash": source, "native_implementation_hash": certificate["native_implementation_hash"],
        "environment_hash": certificate["environment_hash"], "catalog_hash": catalog_hash(root),
        "parent_certificate_hash": certificate["reuse"]["parent_certificate_hash"],
        "parent_certificate_id": certificate["reuse"]["parent_certificate_id"],
        "native_launches": 0, "game_resets": 0, "paid_calls": 0,
        "restoration_certified": False, "cases": [],
    }
    try:
        with worker_guards(root, baseline_root):
            load_session()
            with session_factory(config.environment, reason="startup") as session:
                report["native_launches"] = 1
                _collect_starts(config.environment, session, report, certificate.get("profile_hashes", {}),
                                on_progress)
                # Existing file/identity gates still own runtime integrity. A
                # selection audit never edits instrumentation or the native profile.
                require(digest(session.bridge.verify_files()) == report["environment_hash"],
                        "CONFIGURATION_ENVIRONMENT_CHANGED")
            _unchanged(root, baseline_root, report)
        report["status"] = "passed"
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        # Never serialize arbitrary exception text, including uppercase private
        # values in KeyError/ValueError. NativeFailure separates its public code
        # from the bridge's private raw_message.
        code = error.code if isinstance(error, NativeFailure) else str(error)
        report["reason"] = code if isinstance(error, NativeFailure) or code in COLLECTION_ERRORS else type(error).__name__
    atomic_json(report_path, report, immutable=True)
    return {"status": report["status"], "reason": report.get("reason"),
            "configurations_checked": len(report["cases"]), "native_launches": report["native_launches"],
            "game_resets": report["game_resets"], "paid_calls": 0, "report": str(report_path)}


def _collect_starts(environment, session, report, known_profiles, on_progress):
    configurations = pairs()
    for deck, stake in configurations:
        chosen = environment.model_copy(update={"deck": deck, "stake": stake})
        game = session.new_game(chosen, uuid.uuid4().hex[:8].upper())
        report["game_resets"] += 1
        try:
            row = sample(game, deck, stake, report["environment_hash"])
            expected = known_profiles.get(deck + "/" + stake)
            require(expected is None or row["profile_hash"] == expected, "CERTIFIED_PROFILE_CHANGED")
            report["cases"].append(row)
            completed = len(report["cases"])
            if on_progress and (completed % 10 == 0 or completed == len(configurations)):
                on_progress(completed, len(configurations))
        finally:
            game.close()


def _unchanged(root, baseline_root, report):
    require(implementation_fingerprint() == report["implementation_hash"]
            and catalog_hash(root) == report["catalog_hash"], "SOURCE_CHANGED_DURING_COLLECTION")
    require(lock_digest(root) == report["environment_hash"], "CONFIGURATION_ENVIRONMENT_CHANGED")
    parent, _ = reuse._read_certificate(baseline_root)
    require(digest(parent) == report["parent_certificate_hash"], "ACTIVE_CERTIFICATE_CHANGED")
