import importlib.util
import json
import sys

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project

from balatro_horizons.agents.protocol import Operation, context, helper
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.fake import FakeGame

spec = importlib.util.spec_from_file_location(
    "probe_recorded_context", ROOT / "scripts/probe_recorded_context.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("leaked_seed", [False, True])
def test_probe_spending_is_one_call_no_actions_and_scanned(store, monkeypatch, capsys, leaked_seed):
    cfg = load_config(ROOT / "configs/luna-tools-smoke.yaml")
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    obs = project(game.observe_private())
    if leaked_seed:
        obs.memory = "NEVER_EXPORT_THIS_SEED"
    source = store.create(
        {"agent": "luna", "evidence_kind": "SYNTHETIC_TEST", "config": cfg.public()},
        {"seed": "NEVER_EXPORT_THIS_SEED"},
    )
    obs.episode_id = source
    op = {"kind": "inspect", "sections": ["hand", "public_deck_knowledge"]}
    exchanges = [
        {
            "operation": op,
            "result": helper(Operation.validate_python(op), [], {}, obs),
            "tool_call": {"name": "inspect_state", "arguments": {"sections": op["sections"]}},
        }
    ]
    store.append(source, "observation", obs.model_dump(mode="json"), observation_id=0)
    store.append(
        source,
        "agent_context",
        {"context": context(obs, interface="tools_v2"), "exchanges": exchanges},
        observation_id=0,
    )
    store.finish(source, {"outcome": "GAME_LOSS", "reason": "SYNTHETIC_FIXTURE"})
    head = store.summary(source)["journal_head"]
    calls = []

    def receive(request):
        body = json.loads(request.content)
        calls.append(body)
        assert "NEVER_EXPORT_THIS_SEED" not in json.dumps(body)
        assert "sections_read" in body["input"][-1]["output"]
        args = {
            "observation_id": 0,
            "card_ids": [obs.state.hand[0].id],
            "memory_update": None,
            "decision_note": None,
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "output": [
                    {"type": "function_call", "name": "play_hand", "arguments": json.dumps(args)}
                ],
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    monkeypatch.setattr(probe, "ROOT", store.root.parent)
    monkeypatch.setattr(probe, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        probe,
        "DirectProvider",
        lambda model, limits: DirectProvider(
            model, limits, client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive)))
        ),
    )
    monkeypatch.setattr(
        sys, "argv", ["probe", "--episode-id", source, "--decision", "0", "--allow-paid"]
    )
    ledger = store.root.parent / "private/openai-luna-smoke/spending.json"
    if leaked_seed:
        with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
            probe.main()
        assert calls == [] and not ledger.exists()
        return
    assert probe.main() == 0 and len(calls) == 1
    entries = json.loads(ledger.read_text())
    assert len(entries) == 1
    assert sum(e["cost"] for e in entries.values()) == pytest.approx(0.000044)
    assert store.summary(source)["journal_head"] == head
    new = next(e for e in store.list_episodes() if e["episode_id"] != source)
    assert not new["manifest"]["evaluation_eligible"]
    assert new["summary"]["committed_actions"] == 0
    assert not any(
        e["type"] in ("action_intent", "action_commit") for e in store.events(new["episode_id"])
    )
    assert "NEVER_EXPORT_THIS_SEED" not in capsys.readouterr().out
