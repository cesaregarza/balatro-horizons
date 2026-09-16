"""The live diagnostic is bounded, repeat-protected, and never plays its actions."""

import importlib.util
import json
import sys

import pytest
from test_boundary import project

from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.fake import FakeGame


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cache_probe", ROOT / "scripts/probe_prompt_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source(store):
    cfg = load_config(ROOT / "configs/terra-cache-probe.yaml")
    eid = store.create(
        {"agent": "terra-cache", "evidence_kind": "SYNTHETIC_TEST", "config": cfg.public()}
    )
    game = FakeGame()
    for i, phase in enumerate(("BLIND_SELECT", "SELECTING_HAND", "BLIND_SELECT", "SHOP")):
        game.phase = phase
        obs = project(game.observe_private())
        obs.episode_id, obs.observation_id = eid, i
        store.append(eid, "observation", obs.model_dump(mode="json"), observation_id=i)
        exchanges = []
        if i == 2:
            exchanges = [
                {
                    "operation": {"kind": "arithmetic", "expression": "2*3"},
                    "tool_call": {"name": "calculate", "arguments": {"expression": "2*3"}},
                    "result": {"value": "6", "game_advanced": False},
                }
            ]
        ctx, delivered = decision_context(obs, exchanges, interface="tools_v4")
        body = DirectProvider(cfg.models["terra-cache"], cfg.budgets).request(ctx, delivered)
        store.append(
            eid, "agent_context", {"context": ctx, "exchanges": delivered}, observation_id=i
        )
        store.append(
            eid,
            "provider_request",
            {"body": body, "reserved_usd": 0.1},
            observation_id=i,
            request_id=str(i),
        )
    return eid


def test_preflight_does_not_send_or_touch_source_and_paid_probe_reserves_once(
    store, tmp_path, monkeypatch, probe
):
    eid = source(store)
    before = store.events(eid)
    campaign = tmp_path / "probe"
    args = [
        "probe",
        "--root",
        str(store.root),
        "--episode-id",
        eid,
        "--config",
        str(ROOT / "configs/terra-cache-probe.yaml"),
        "--model",
        "terra-cache",
        "--campaign",
        str(campaign),
    ]
    monkeypatch.setattr(sys, "argv", args)

    def no_send(*args):
        raise AssertionError("preflight must not send")

    monkeypatch.setattr(DirectProvider, "send", no_send)
    assert probe.main() == 0
    assert not campaign.exists()
    assert store.events(eid) == before
    calls = []

    def send(self, body):
        calls.append(body)
        return {
            "id": "response_" + str(len(calls)),
            "status": "completed",
            "output": [
                {"type": "function_call", "name": "calculate", "arguments": '{"expression":"2*3"}'}
            ],
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 100,
                "input_tokens_details": {
                    "cached_tokens": 0 if len(calls) == 1 else 3000,
                    "cache_write_tokens": 3000 if len(calls) == 1 else 0,
                },
            },
        }

    monkeypatch.setattr(DirectProvider, "send", send)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(sys, "argv", args + ["--allow-paid"])
    assert probe.main() == 0
    assert len(calls) == 4
    assert all(b["tools"] == calls[0]["tools"] for b in calls)
    assert calls[1]["prompt_cache_options"]["comparison_response_id"] == "response_1"
    ledger = json.loads((campaign / "spending.json").read_text())
    assert len(ledger) == 4 and all(row["settled"] for row in ledger.values())
    assert sum(row["reserved"] for row in ledger.values()) < 1
    assert store.events(eid) == before
    with pytest.raises(ValueError, match="ALREADY_STARTED"):
        probe.main()
    assert len(calls) == 4
