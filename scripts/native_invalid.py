#!/usr/bin/env python3
"""Prove rejected native consumable selections leave the game unchanged."""

import json
import uuid

from native_acceptance import Audit

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.contracts import ActionEnvelope
from balatro_horizons.evidence.provenance import continuation_fingerprint
from balatro_horizons.game.contract import NativeRejected
from balatro_horizons.game.session import NativeSession
from balatro_horizons.storage.journal import atomic_json


def collect(config, *, game_factory=None):
    audit = Audit(config, "invalid_consumables", game_factory=game_factory)
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
        return result
    except Exception:
        audit.finish("NATIVE_FIXTURE_FAILED")
        raise


def main(*, game_factory=None, session_factory=NativeSession):
    config = load_config(ROOT / "configs/smoke.yaml")
    if game_factory is None:
        with session_factory(config.environment, reason="startup") as session:
            return collect(config, game_factory=session.new_game)
    return collect(config, game_factory=game_factory)


if __name__ == "__main__":
    main()
