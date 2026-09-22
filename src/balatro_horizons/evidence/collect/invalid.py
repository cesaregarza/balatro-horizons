"""Collect rejection evidence for disabled and wrongly-targeted consumables."""

from __future__ import annotations

import json
import uuid
from typing import Any

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope
from balatro_horizons.evidence.collect.acceptance import Audit
from balatro_horizons.evidence.provenance import continuation_fingerprint
from balatro_horizons.game.contract import NativeRejected
from balatro_horizons.storage.journal import atomic_json


def collect(config: Any, *, game_factory=None) -> dict:
    audit = Audit(config, "invalid_consumables", game_factory=game_factory)
    try:
        blind = audit.obs.state.revealed_blinds[0].id
        audit.take({"type": "select_blind", "blind_id": blind})
        audit.fixture("invalid_consumables")
        ankh, aura = audit.obs.state.consumables
        assert not ankh.usable and aura.usable
        invalid = ActionEnvelope.model_validate(
            {
                "observation_id": audit.decision,
                "action": {"type": "use_consumable", "consumable_id": ankh.id, "target_ids": []},
            }
        )
        _assert_disabled_rejected(invalid, audit)
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
        except NativeRejected:
            pass
        else:
            raise AssertionError("Editioned Aura target was allowed")
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


def _assert_disabled_rejected(envelope: ActionEnvelope, audit: Audit) -> None:
    try:
        validate_action(envelope, audit.obs)
    except InvalidAction:
        return
    raise AssertionError("Disabled Ankh was allowed")
