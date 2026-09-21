"""Certify every traversed prefix boundary of a complete seed replay."""

from __future__ import annotations

import json
import uuid

from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import (
    certificate_path,
    read_checkpoint,
    steps_for,
    verify_checkpoint,
)
from balatro_horizons.storage.journal import Store, atomic_json, digest


def _derived_certificate(store: Store, episode_id: str, decision: int, cert: dict, steps: list[dict]) -> dict:
    checkpoint = read_checkpoint(store, episode_id, decision)
    suffix = [
        step for step in steps if step["observation"]["observation_id"] > decision
    ]
    return {
        **cert,
        "certificate_id": uuid.uuid4().hex,
        "decision": decision,
        "phase": checkpoint["observation"]["phase"],
        "checkpoint_hash": digest(checkpoint),
        "suffix_hash": digest(suffix),
        "derived_from": cert["certificate_id"],
        "derivation": "boundary traversed and compared in three complete seed replays",
    }


def certify_prefix(store: Store, episode_id: str, *, emit: bool = False) -> dict:
    config = Config.model_validate(store.manifest(episode_id, True)["config"])
    cert = verify_checkpoint(store, config, episode_id, 0, mode="seed_prefix")
    if emit:
        print(json.dumps(cert), flush=True)
    if cert["status"] != "passed":
        return cert
    steps = steps_for(store, episode_id)
    for path in sorted(store.episode_path(episode_id, True).glob("checkpoint-*.json")):
        decision = int(path.stem.split("-")[1])
        suffix = [step for step in steps if step["observation"]["observation_id"] > decision]
        if decision == 0 or not any(step["kind"] == "action" for step in suffix):
            continue
        derived = _derived_certificate(store, episode_id, decision, cert, steps)
        target = certificate_path(store, episode_id, decision)
        atomic_json(
            target.with_name("certificate-record-" + derived["certificate_id"] + ".json"),
            derived,
            immutable=True,
        )
        atomic_json(target, derived)
        print(
            json.dumps(
                {
                    "decision": decision,
                    "phase": derived["phase"],
                    "mode": "seed_prefix",
                    "status": "passed",
                }
            ),
            flush=True,
        )
    return cert
