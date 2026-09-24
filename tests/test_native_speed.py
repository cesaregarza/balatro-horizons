"""Pin visible 16x execution separately from headless/fast shortcuts."""

import re

import pytest
from native_dispatch_harness import NativeHarness

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.publish import _certificate
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import VISIBLE_GAME_SPEED, verify_identity


def identity(**changes):
    return {"bh": {
        "identity": "BalatroHorizons", "save_directory": "/isolated/BalatroHorizons",
        "calibration": False, "profile_policy": "fully_unlocked_v1",
        "headless": False, "fast": False, "gamespeed": VISIBLE_GAME_SPEED, **changes,
    }}


def test_launcher_uses_visible_16x_without_fast_or_headless():
    source = (ROOT / "native/bridge.ps1").read_text()
    values = dict(re.findall(r"\$env:(BALATROBOT_[A-Z_]+) = '([^']*)'", source))
    assert values["BALATROBOT_GAMESPEED"] == str(VISIBLE_GAME_SPEED) == "16"
    assert values["BALATROBOT_FAST"] == values["BALATROBOT_HEADLESS"] == "0"
    assert values["BALATROBOT_ANIMATION_FPS"] == "60"


@pytest.mark.parametrize("speed", [1, 4, 16, 32])
def test_private_lua_identity_reports_actual_game_speed(speed):
    harness = NativeHarness()
    harness.execute(f"G.SETTINGS.GAMESPEED = {speed}; BB_SETTINGS.gamespeed = 999")
    result = harness.request("bh_inspect")
    assert result["bh"]["gamespeed"] == speed


def test_visible_16x_identity_is_accepted():
    verify_identity(identity())


@pytest.mark.parametrize("speed", [None, 1, 4, 8, 32, "16", True])
def test_missing_or_different_speed_is_refused(speed):
    state = identity(gamespeed=speed)
    if speed is None:
        del state["bh"]["gamespeed"]
    with pytest.raises(NativeFailure, match="RUNTIME_GAME_SPEED_MISMATCH"):
        verify_identity(state)


@pytest.mark.parametrize("mode", ["headless", "fast"])
def test_16x_does_not_admit_fast_or_headless(mode):
    with pytest.raises(NativeFailure, match="RUNTIME_POLICY_MISMATCH"):
        verify_identity(identity(**{mode: True}))


def test_new_certificate_names_exact_speed_and_disabled_shortcuts():
    cert = _certificate("environment", "source", "native", {}, set())
    assert cert["gamespeed"] == VISIBLE_GAME_SPEED == 16
    assert cert["accelerated"] is True
    assert cert["headless"] is cert["fast"] is False
    assert cert["paid_provider_validation"] is False
