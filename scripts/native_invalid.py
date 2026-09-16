#!/usr/bin/env python3
"""Prove rejected native consumable selections leave the game unchanged."""

import json
import uuid

from native_acceptance import Audit

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.contracts import ActionEnvelope
from balatro_horizons.engine.native import NativeRejected
from balatro_horizons.engine.provenance import continuation_fingerprint
from balatro_horizons.storage.journal import atomic_json


def main():
    audit = Audit(load_config(ROOT / "configs/smoke.yaml"), "invalid_consumables")
    try:
        audit.take({"type": "select_blind", "blind_id": audit.obs.state.revealed_blinds[0].id})
        audit.fixture("invalid_consumables")
        ankh, aura = audit.obs.state.consumables
        assert not ankh.usable and aura.usable
        invalid = ActionEnvelope.model_validate(
            {
                "observation_id": audit.decision,
                "action": {"type": "use_consumable", "consumable_id": ankh.id, "target_ids": []},
            }
        )
        try:
            validate_action(invalid, audit.obs)
            raise AssertionError("Disabled Ankh was allowed")
        except InvalidAction:
            pass
        before = continuation_fingerprint(audit.game.observe_private())
        wrong_target = ActionEnvelope.model_validate(
            {
                "observation_id": audit.decision,
                "action": {
                    "type": "use_consumable",
                    "consumable_id": aura.id,
                    "target_ids": [audit.obs.state.hand[1].id],
                },
            }
        )
        try:
            audit.game.apply_public_action(wrong_target.action, audit.issuer, uuid.uuid4().hex)
            raise AssertionError("Editioned Aura target was allowed")
        except NativeRejected:
            pass
        audit.game.wait_ready()
        assert continuation_fingerprint(audit.game.observe_private()) == before
        result = {
            "episode_id": audit.eid,
            "disabled_consumable_rejected": True,
            "invalid_target_rejected": True,
            "unchanged": True,
        }
        audit.finish()
        atomic_json(ROOT / "reports/verification/native-invalid.json", result)
        print(json.dumps(result), flush=True)
    except Exception:
        audit.finish("NATIVE_FIXTURE_FAILED")
        raise


if __name__ == "__main__":
    main()
