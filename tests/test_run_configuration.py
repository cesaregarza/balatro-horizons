from itertools import product
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.api.models import RunInput
from balatro_horizons.api.routes_runs import require_native_selection
from balatro_horizons.config import Config
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.run_configuration import OPTIONS


def test_catalog_contains_every_standard_choice_once():
    assert len(OPTIONS["decks"]) == len(set(OPTIONS["decks"])) == 15
    assert len(OPTIONS["stakes"]) == len(set(OPTIONS["stakes"])) == 8
    assert OPTIONS["decks"][-1] == "ERRATIC"
    assert OPTIONS["stakes"] == ["WHITE", "RED", "GREEN", "BLACK", "BLUE", "PURPLE", "ORANGE", "GOLD"]


def test_every_pair_reaches_launch_without_changing_saved_defaults(store):
    app = create_app(store.root, Config())
    start = Mock(return_value="a" * 32)
    app.state.runs.start = start
    before = app.state.config.model_dump()
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        for deck, stake in product(OPTIONS["decks"], OPTIONS["stakes"]):
            reply = client.post("/api/runs", headers=headers, json={"deck": deck, "stake": stake})
            assert reply.status_code == 200
            chosen = start.call_args.args[0]
            assert (chosen.environment.deck, chosen.environment.stake) == (deck, stake)
            assert chosen.budgets == app.state.config.budgets
            assert start.call_args.kwargs == {"offline": True, "calibration": False}
            assert app.state.config.model_dump() == before
    assert start.call_count == 120
    assert not app.state.settings_path.exists()


@pytest.mark.parametrize("payload", [
    {"deck": "NOT_A_DECK"}, {"deck": "red"}, {"deck": ""}, {"deck": 3},
    {"stake": "BOSS"}, {"stake": "white"}, {"stake": ""}, {"stake": 1},
    {"preset": "smoke", "deck": "BLUE"}, {"preset": "pilot", "stake": "WHITE"},
])
def test_unknown_and_ambiguous_choices_refuse_before_launch(store, payload):
    app = create_app(store.root, Config())
    app.state.runs.start = Mock(side_effect=AssertionError("must not start"))
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        assert client.post("/api/runs", headers=headers, json=payload).status_code == 422
    app.state.runs.start.assert_not_called()
    assert store.list_episodes() == []


@pytest.mark.parametrize("payload,expected", [
    ({}, ("BLUE", "PURPLE")), ({"preset": "pilot"}, ("BLUE", "PURPLE")),
    ({"preset": "smoke"}, ("BLUE", "WHITE")), ({"deck": "GHOST"}, ("GHOST", "PURPLE")),
    ({"stake": "ORANGE"}, ("BLUE", "ORANGE")),
])
def test_omitted_fields_and_legacy_presets_keep_their_contract(store, payload, expected):
    config = Config()
    config.environment.deck, config.environment.stake = "BLUE", "PURPLE"
    app = create_app(store.root, config)
    app.state.runs.start = Mock(return_value="b" * 32)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        assert client.post("/api/runs", headers=headers, json=payload).status_code == 200
    chosen = app.state.runs.start.call_args.args[0].environment
    assert (chosen.deck, chosen.stake) == expected


def test_new_choice_is_frozen_in_both_run_manifests(store, monkeypatch):
    app = create_app(store.root, Config())
    monkeypatch.setattr(app.state.runs, "_launch", Mock())
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        response = client.post("/api/runs", headers=headers, json={"deck": "ERRATIC", "stake": "ORANGE"})
    assert response.status_code == 200
    eid = response.json()["episode_id"]
    public = store.manifest(eid)["config"]
    private = store.manifest(eid, True)["config"]["environment"]
    assert (public["deck"], public["stake"]) == (private["deck"], private["stake"]) == ("ERRATIC", "ORANGE")
    assert app.state.config.environment.deck == "RED" and app.state.config.environment.stake == "GOLD"


def test_native_selection_retains_session_and_certificate_gates(monkeypatch):
    from balatro_horizons.evidence import certification, lock
    from balatro_horizons.game import windows_context

    monkeypatch.setattr(windows_context, "load_session", Mock())
    monkeypatch.setattr(lock, "read_lock", lambda: {"test": "environment"})
    certificate = Mock(side_effect=NativeFailure("NATIVE_CAPABILITY_CERTIFICATE_MISMATCH"))
    monkeypatch.setattr(certification, "require_environment_certificate", certificate)
    config = Config()
    config.environment.deck = "PLASMA"
    with pytest.raises(ValueError, match="^NATIVE_CAPABILITY_CERTIFICATE_MISMATCH$"):
        require_native_selection(config.environment)
    certificate.assert_called_once_with({"test": "environment"}, config.environment)


def test_cost_confirmation_cannot_be_bypassed_by_game_choices():
    with pytest.raises(ValueError, match="UNCAPPED_CONFIRMATION_REQUIRED"):
        RunInput(deck="PLASMA", stake="GOLD", cost_override="uncapped")
