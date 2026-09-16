"""Offline legality and installer regressions; native certification is separate."""

import hashlib
import importlib.util
from pathlib import Path

import pytest
from test_boundary import project

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.protocol import context
from balatro_horizons.contracts import ActionEnvelope
from balatro_horizons.engine.native import NativeGame
from balatro_horizons.engine.native_state import normalize
from balatro_horizons.observations.projection import HandleIssuer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_patches", ROOT / "scripts/native_patches.py")
patches = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patches)


def state(phase):
    return {
        "state": phase,
        "bh": {"blind_on_deck": "Big"},
        "blinds": {"big": {"name": "Big Blind", "score": 450}},
        **{
            area: {"cards": [{"id": i, "label": f"{area}-{i}"} for i in range(2)]}
            for area in ("hand", "jokers", "consumables")
        },
    }


@pytest.mark.parametrize(
    "phase", ["BLIND_SELECT", "ROUND_EVAL", "SHOP", "SELECTING_HAND", "SMODS_BOOSTER_OPENED"]
)
def test_owned_ordering_is_advertised_validated_and_sent_once(phase):
    raw = state(phase)
    issuer = HandleIssuer(b"r" * 32)
    obs = project(normalize(raw), issuer)
    expected = ["jokers", "consumables"]
    if phase in ("SELECTING_HAND", "SMODS_BOOSTER_OPENED"):
        expected.insert(0, "hand")
    assert obs.action_constraints["reorder"]["areas"] == expected
    for interface in ("tools_v2", "tools_v3"):
        schema = next(
            tool for tool in context(obs, interface=interface)["tools"] if tool["name"] == "reorder"
        )
        assert schema["parameters"]["properties"]["area"]["enum"] == expected
    calls = []

    class Bridge:
        def rpc(self, method, params, request_id):
            calls.append((method, params, request_id))

    game = NativeGame.__new__(NativeGame)
    game.raw, game.bridge = raw, Bridge()
    game.wait_ready = lambda: None
    for area in expected:
        action = ActionEnvelope.model_validate(
            {
                "observation_id": 0,
                "action": {
                    "type": "reorder",
                    "area": area,
                    "ordered_ids": [card.id for card in getattr(obs.state, area)][::-1],
                },
            }
        )
        validate_action(action, obs)
        game.apply_public_action(action.action, issuer, request_id=area)
    assert calls == [("rearrange", {area: [1, 0]}, area) for area in expected]


@pytest.mark.parametrize("phase", ["BLIND_SELECT", "ROUND_EVAL", "SHOP"])
def test_unavailable_hand_and_empty_areas_are_rejected_before_native_execution(phase):
    raw = state(phase)
    raw["consumables"]["cards"] = []
    obs = project(normalize(raw))
    assert obs.action_constraints["reorder"]["areas"] == ["jokers"]
    for area in ("hand", "consumables"):
        action = ActionEnvelope.model_validate(
            {
                "observation_id": 0,
                "action": {
                    "type": "reorder",
                    "area": area,
                    "ordered_ids": [card.id for card in getattr(obs.state, area)],
                },
            }
        )
        with pytest.raises(InvalidAction, match="REORDER_AREA_NOT_AVAILABLE"):
            validate_action(action, obs)


@pytest.mark.parametrize("phase", ["MENU", "GAME_OVER", "HAND_PLAYED", "TAROT_PACK"])
def test_unsupported_phases_do_not_offer_ordering(phase):
    assert "reorder" not in project(normalize(state(phase))).available_action_types


def test_installer_patches_admission_and_both_owned_completion_predicates():
    upstream = ROOT / "vendor/balatrobot/src/lua/endpoints/rearrange.lua"
    if not upstream.is_file():
        pytest.skip("Pinned BalatroBot checkout is not cached locally")
    original = upstream.read_text()
    assert hashlib.sha256(original.encode()).hexdigest() == patches.REARRANGE_SHA256
    patched = patches.patch_rearrange(original)
    assert patched.count("G.STATES.BLIND_SELECT") == 3
    assert patched.count("G.STATES.ROUND_EVAL") == 3
    assert (
        "if G.STATE ~= G.STATES.SELECTING_HAND and G.STATE ~= G.STATES.SMODS_BOOSTER_OPENED then"
        in patched
    )
    assert upstream.read_text() == original
    for unexpected in (original + "-- upstream drift", patched):
        with pytest.raises(ValueError, match="UNEXPECTED_REARRANGE_SOURCE"):
            patches.patch_rearrange(unexpected)
