"""Surviving cache-audit and probe scripts use synthetic journals and mocked sends only."""

import importlib
import importlib.util
import json
import sys

import pytest
from test_boundary import project

from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context

PRIVATE_SEED = "NEVER_EXPORT_THIS_SEED"


def load_probe(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cache_probe", ROOT / "scripts/probe_prompt_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_config():
    config = load_config(ROOT / "configs/luna-smoke.yaml")
    config.skills = "none"
    config.budgets.max_provider_calls = 4
    config.budgets.max_transport_attempts = 1
    config.budgets.max_batch_cost_usd = 1
    return config


def source(store, config, *, leaked_seed=False):
    eid = store.create(
        {"agent": "luna", "evidence_kind": "SYNTHETIC_TEST", "config": config.public()},
        {"seed": PRIVATE_SEED},
    )
    game = FakeGame()
    policy = DirectProvider(config.models["luna"], config.budgets)
    try:
        for index, phase in enumerate(("BLIND_SELECT", "SELECTING_HAND", "BLIND_SELECT", "SHOP")):
            game.phase = phase
            observation = project(game.observe_private())
            observation.episode_id, observation.observation_id = eid, index
            observation.memory = PRIVATE_SEED  # Current harness never delivers this legacy field.
            store.append(
                eid,
                "observation",
                observation.model_dump(mode="json"),
                observation_id=index,
            )
            exchanges = []
            if index == 2:
                exchanges = [
                    {
                        "operation": {"kind": "arithmetic", "expression": "2*3"},
                        "tool_call": {"name": "calculate", "arguments": {"expression": "2*3"}},
                        "result": {
                            "result": PRIVATE_SEED if leaked_seed else "6",
                            "game_advanced": False,
                        },
                    }
                ]
            context, delivered = decision_context(observation, exchanges)
            body = policy.request(context, delivered)
            store.append(
                eid,
                "agent_context",
                {"context": dict(context), "exchanges": delivered},
                observation_id=index,
            )
            store.append(
                eid,
                "provider_request",
                {"body": body, "reserved_usd": 0.1},
                observation_id=index,
                request_id=str(index),
            )
    finally:
        policy.client.close()
    return eid


def test_cache_layout_audits_current_context_without_mutating_source(store, monkeypatch):
    config = probe_config()
    eid = source(store, config)
    before = store.events(eid)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    audit = importlib.import_module("audit_cache_layout")
    rows = audit.candidates(audit.readonly_store(store.root), eid, config, "luna")
    assert [row["phase"] for row in rows] == [
        "BLIND_SELECT", "SELECTING_HAND", "BLIND_SELECT", "SHOP"
    ]
    assert all(row["body"]["tool_choice"] == "auto" for row in rows)
    assert len({json.dumps((row["body"]["tools"], row["body"]["input"][0])) for row in rows}) == 1
    assert "calculate" in rows[2]["allowed_tools"]
    report = audit.compare(audit.readonly_store(store.root), eid, config, "luna")
    assert report["request_count"] == 4 and report["after"]["unique_prefixes"] == 1
    assert report["source_journal_head"] == before[-1]["hash"]
    assert store.events(eid) == before
    assert PRIVATE_SEED not in json.dumps(report)


def test_probe_preflight_never_sends_and_mocked_probe_reserves_once_per_call(
    store, tmp_path, monkeypatch, capsys
):
    config = probe_config()
    eid = source(store, config)
    before = store.events(eid)
    campaign = tmp_path / "probe"
    probe = load_probe(monkeypatch)
    monkeypatch.setattr(probe, "load_config", lambda path: config)
    args = [
        "probe", "--root", str(store.root), "--episode-id", eid,
        "--config", str(ROOT / "configs/luna-smoke.yaml"),
        "--model", "luna", "--campaign", str(campaign),
    ]
    monkeypatch.setattr(sys, "argv", args)

    def no_send(*args):
        raise AssertionError("preflight must not send")

    monkeypatch.setattr(DirectProvider, "send", no_send)
    assert probe.main() == 0
    assert not campaign.exists() and store.events(eid) == before
    assert PRIVATE_SEED not in capsys.readouterr().out
    calls = []

    def send(self, body):
        calls.append(body)
        return {
            "id": "response_" + str(len(calls)),
            "status": "completed",
            "output": [{
                "type": "function_call", "call_id": f"mock_{len(calls)}",
                "name": "calculate", "arguments": '{"expression":"2*3"}',
            }],
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
    monkeypatch.setattr(
        DirectProvider,
        "check_input",
        lambda self, body: {"method": "synthetic_test_count", "input_tokens": 5000},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(sys, "argv", args + ["--allow-paid"])
    assert probe.main() == 0
    assert len(calls) == 4
    assert all(body["tools"] == calls[0]["tools"] for body in calls)
    assert calls[1]["prompt_cache_options"]["comparison_response_id"] == "response_1"
    ledger = json.loads((campaign / "spending.json").read_text())
    assert len(ledger) == 4 and all(row["settled"] for row in ledger.values())
    assert sum(row["reserved"] for row in ledger.values()) < 1
    summary = json.loads((campaign / "summary.json").read_text())
    assert summary["game_launches"] == summary["game_actions"] == 0
    assert store.events(eid) == before
    assert PRIVATE_SEED not in capsys.readouterr().out
    with pytest.raises(ValueError, match="PROBE_CAMPAIGN_ALREADY_STARTED"):
        probe.main()
    assert len(calls) == 4
