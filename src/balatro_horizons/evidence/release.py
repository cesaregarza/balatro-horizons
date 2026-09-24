"""Release certification for collected restoration and branch evidence."""

import json
from pathlib import Path

from balatro_horizons.config import ROOT, Config
from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.evidence.certification import (
    continuation_probe,
    read_checkpoint,
    require_checkpoint_certificate,
)
from balatro_horizons.evidence.lock import lock_digest
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.evidence.stages import BRANCH_RESTORATION, RESTORATION_CERTIFICATION
from balatro_horizons.storage.journal import Store, atomic_json, digest


def _implementation_annotation(store, review, eid):
    review.expose(
        eid,
        "implementation_verification",
        outcome_seen=True,
        model_identity_seen=True,
        max_event_seen=len(store.events(eid)) - 1,
    )
    session = review.open_explorer(eid)
    annotation = review.annotate(
        session["review_token"],
        AnnotationInput.model_validate(
            {
                "start_decision": 0,
                "end_decision": 0,
                "judgment": "unclear",
                "confidence": "low",
                "horizons_in_tension": ["near_term", "long_term"],
                "mechanism_summary": "Implementation verification annotation, not an expert assessment: compare selecting the blind with a skip continuation.",
                "alternative_actions": ["Skip the current blind and let the same baseline continue."],
            }
        ),
    )
    return session, annotation


def _branch_restoration(store, config, gold):
    from balatro_horizons.service import RunService
    from balatro_horizons.workbench.service import WorkbenchService

    review = WorkbenchService(store)
    session, annotation = _implementation_annotation(store, review, gold)
    observation = session["view"]["observation"]
    before = store.summary(gold)["journal_head"]
    service = RunService(store, review)
    child = service.branch(
        config,
        gold,
        0,
        "single_action_override",
        [{"type": "skip_blind", "blind_id": observation["state"]["revealed_blinds"][0]["id"]}],
        calibration=True,
    )
    service.thread.join()
    summary = store.summary(child)
    if not summary or summary["outcome"] not in ("WIN", "GAME_LOSS"):
        raise ValueError("BRANCH_NOT_COMPLETE")
    if store.summary(gold)["journal_head"] != before:
        raise ValueError("PARENT_JOURNAL_CHANGED")
    if store.manifest(child)["evaluation_eligible"]:
        raise ValueError("ASSISTED_BRANCH_SCORED")
    return child, summary, annotation["annotation_id"]


def _settlement_evidence(root, store, release):
    from balatro_horizons.evidence.collect.settlement import verify

    episode_ids = [release["fixture"]["episode_id"]]
    episode_ids.extend(run["episode_id"] for run in release["ordinary_runs"].values())
    faults = json.loads((root / "reports/verification/native-faults.json").read_text())
    episode_ids.extend(run["episode_id"] for run in faults["tests"])
    reorder = json.loads((root / "reports/verification/native-reorder.json").read_text())
    if reorder["implementation_hash"] != release["implementation_hash"]:
        raise ValueError("STALE_REORDER_SOURCE")
    if reorder["environment_hash"] != release["environment_hash"]:
        raise ValueError("STALE_REORDER_ENVIRONMENT")
    episode_ids.append(reorder["episode_id"])
    for episode_id in episode_ids:
        checkpoint = read_checkpoint(store, episode_id, 0)
        if checkpoint.get("implementation_hash") != release["implementation_hash"]:
            raise ValueError("STALE_SETTLEMENT_SOURCE")
        if digest(checkpoint["game"]["environment"]) != release["environment_hash"]:
            raise ValueError("STALE_SETTLEMENT_ENVIRONMENT")
    checks = verify(store, episode_ids)
    result = {
        "status": "passed",
        "evidence_kind": "NATIVE_CALIBRATION",
        "implementation_hash": release["implementation_hash"],
        "environment_hash": release["environment_hash"],
        "checks": checks,
        "provider_calls": 0,
        "additional_game_launches": 0,
    }
    atomic_json(root / "reports/verification/native-settlement.json", result)
    return result


def _restoration_certificates(store, config, fixture, gold, *, branch_only=False):
    if branch_only:
        _, direct = require_checkpoint_certificate(store, gold, 0)
        return direct
    for episode_id in (fixture["episode_id"], gold):
        continuation_probe(store, config, episode_id, restoration="seed_prefix")
    # A failed direct check leaves the separately verified seed-prefix capability intact.
    return continuation_probe(store, config, gold, restoration="checkpoint")


def certify_release(root=None, *, from_stage=None):
    """Certify collected restoration boundaries and one immutable-parent branch."""
    if from_stage not in (None, RESTORATION_CERTIFICATION, BRANCH_RESTORATION):
        raise ValueError("UNKNOWN_CERTIFICATION_STAGE")
    root = Path(ROOT if root is None else root).resolve()
    source = implementation_fingerprint()
    store = Store(root / "data")
    collected = json.loads((root / "reports/verification/native-fixtures-final.json").read_text())
    runs = json.loads((root / "reports/verification/native-runs.json").read_text())
    fixture, gold = collected["actions"], runs["pilot"]["episode_id"]
    config = Config.model_validate(store.manifest(gold, True)["config"])
    direct = _restoration_certificates(
        store,
        config,
        fixture,
        gold,
        branch_only=from_stage == BRANCH_RESTORATION,
    )
    child, branch, annotation_id = _branch_restoration(store, config, gold)
    current_source = implementation_fingerprint()
    if current_source != source:
        raise ValueError("SOURCE_CHANGED_DURING_CERTIFICATION")
    result = {
        "fixture": fixture,
        "win_fixture": collected["win"],
        "ordinary_runs": runs,
        "branch": branch,
        "parent_episode_id": gold,
        "parent_unchanged": True,
        "annotation_id": annotation_id,
        "direct_checkpoint": direct,
        "environment_hash": lock_digest(root),
        "collection_started_with_implementation_hash": source,
        "implementation_hash": current_source,
    }
    atomic_json(root / "reports/verification/native-release.json", result)
    _settlement_evidence(root, store, result)
    return {"native_release_evidence": True, "branch": child, "parent": gold}
