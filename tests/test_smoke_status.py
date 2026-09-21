import importlib.util
import json
import sys

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.harness.money import Spending
from balatro_horizons.review.service import ReviewService

spec = importlib.util.spec_from_file_location("smoke_openai", ROOT / "scripts/smoke_openai.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_status_is_scoped_public_and_retains_unknown_reservations(store):
    eid = store.create(
        {
            "agent": "luna",
            "evidence_kind": "SYNTHETIC_TEST",
            "validation_purpose": "openai_luna_smoke",
        },
        {"seed": "PRIVATE_SENTINEL"},
    )
    unrelated = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(
        eid,
        "observation",
        {
            "phase": "SELECTING_HAND",
            "state": {
                "progress": {"ante": 2},
                "hand": [{"label": "DO_NOT_DUMP_STATE"}],
            },
        },
    )
    store.append(eid, "provider_request", {"reserved_usd": 0.1}, request_id="known")
    store.append(eid, "provider_response", {"cost_usd": 0.01}, request_id="known")
    store.append(eid, "provider_request", {"reserved_usd": 0.1}, request_id="unknown")
    store.append(eid, "action_commit", {})
    store.append(
        eid,
        "helper_result",
        {"operation": {"kind": "inspect"}, "result": {"hand": "DO_NOT_DUMP_STATE"}},
    )
    result = smoke.episode_status(store)
    assert result["episode_id"] == eid and result["ante"] == 2
    assert result["settled_cost_usd"] == 0.01 and result["reserved_unknown_usd"] == 0.1
    assert result["provider_calls"] == 2 and result["committed_actions"] == 1
    assert result["helper_calls_by_kind"] == {"inspect": 1}
    assert result["outcome"] is None
    assert "PRIVATE_SENTINEL" not in json.dumps(result)
    assert "DO_NOT_DUMP_STATE" not in json.dumps(result)
    exposure = ReviewService(store).exposure(eid)
    assert exposure["max_event_seen"] == 5 and exposure["model_identity_seen"]
    assert not exposure["outcome_seen"]
    assert not ReviewService(store).exposure(unrelated)["records"]
    store.finish(eid, {"outcome": "GAME_LOSS", "reason": "VERIFIED_ENGINE_TERMINAL"})
    assert smoke.episode_status(store, eid)["outcome"] == "GAME_LOSS"
    assert ReviewService(store).exposure(eid)["outcome_seen"]


def test_status_without_a_smoke_fails_clearly(store):
    with pytest.raises(ValueError, match="NO_LUNA_SMOKE_EPISODE"):
        smoke.episode_status(store)
    with pytest.raises(ValueError, match="INVALID_IDENTIFIER"):
        smoke.episode_status(store, "../private")


def test_generic_smoke_enforces_explicit_caps_before_native_or_paid_execution(monkeypatch):
    from test_openai_luna import luna

    config = luna()
    config.models = {"terra": config.models["luna"]}
    config.models["terra"].model = "gpt-5.6-terra"
    config.budgets.max_episode_cost_usd = 5
    config.budgets.max_batch_cost_usd = 10
    monkeypatch.setattr(smoke, "load_config", lambda _: config)
    monkeypatch.setattr(sys, "argv", ["smoke_openai.py", "--agent", "terra", "--dry-run"])
    with pytest.raises(ValueError, match="SMOKE_EXCEEDS_AUTHORIZED_CAPS"):
        smoke.main()


@pytest.mark.parametrize(
    "episode_cap,campaign_cap,expected",
    [
        (1, 0.0329824, []),
        (1, 0.0329823, ["CAMPAIGN_COST_CAP"]),
        (0.010, 0.005, ["EPISODE_CAP_BELOW_RESERVATION"]),
    ],
)
def test_smoke_preflight_uses_shared_admission_without_paid_execution(
    tmp_path,
    monkeypatch,
    capsys,
    episode_cap,
    campaign_cap,
    expected,
):
    from test_openai_luna import luna

    config = luna()
    config.budgets.max_episode_cost_usd = episode_cap
    config.budgets.max_batch_cost_usd = campaign_cap
    ledger = tmp_path / "private/openai-luna-smoke/spending.json"
    # Existing usage may exceed a newly supplied cap; retain it in diagnostics.
    Spending(ledger, 1).reserve("existing", "previous", 0.01496, 1)
    before = ledger.read_bytes()
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(smoke, "load_config", lambda _: config)
    monkeypatch.setattr(sys, "argv", ["smoke_openai.py", "--transport-only", "--dry-run"])
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    assert smoke.main() == int(bool(expected))
    report = json.loads(capsys.readouterr().out)
    assert report["blockers"] == expected
    assert report["per_request_reserve_usd"] == 0.0180224
    assert report["campaign_accounted_usd"] == 0.01496
    assert report["paid_calls_made"] == 0 and ledger.read_bytes() == before
    assert not config.budgets.paid_calls_enabled
