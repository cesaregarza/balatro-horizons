import json
import uuid

import pytest
from test_boundary import project

from balatro_horizons.agents.protocol import TOOL, context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.batches import cluster_interval, summarize
from balatro_horizons.evaluation.reports import scan


def model(provider):
    return ModelConfig(
        provider=provider,
        model="test-fixed-id",
        input_usd_per_million=1,
        output_usd_per_million=2,
        pricing_date="2026-09-14",
    )


def test_AT15_provider_parity():
    ctx = context(project(FakeGame().observe_private()))
    a = DirectProvider(model("openai"), Limits()).request(ctx, [])
    b = DirectProvider(model("anthropic"), Limits()).request(ctx, [])
    assert a["input"] == b["messages"]
    assert a["instructions"] == b["system"]
    assert a["tools"][0]["parameters"] == b["tools"][0]["input_schema"] == TOOL["parameters"]
    assert a["parallel_tool_calls"] is False
    assert b["tool_choice"]["disable_parallel_tool_use"] is True
    assert "api_key" not in json.dumps(a) + json.dumps(b)


def test_AT15_provider_rejects_multiple_operations():
    p = DirectProvider(model("openai"), Limits())
    with pytest.raises(ProtocolFailure):
        p.parse({"output": [{"type": "function_call", "name": "operate", "arguments": "{}"}] * 2})
    p = DirectProvider(model("anthropic"), Limits())
    assert (
        p.parse(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "operate",
                        "input": {"kind": "abort", "reason": "test"},
                    }
                ]
            }
        )["kind"]
        == "abort"
    )


def attempt(slot, outcome, date, cost=1, branch=False):
    eid = uuid.uuid4().hex
    return {
        "episode_id": eid,
        "manifest": {
            "slot_id": slot,
            "batch_id": "batch",
            "evaluation_eligible": True,
            "created_at": date,
            **({"parent_episode_id": "parent"} if branch else {}),
        },
        "summary": {"outcome": outcome, "cost_usd": cost},
    }


def test_AT19_mixed_status_coverage_and_all_attempt_cost():
    plan = {
        "batch_id": "batch",
        "slots": [
            {"slot_id": str(i), "seed_group": str(i), "agent": "a", "replicate": 0}
            for i in range(5)
        ],
    }
    attempts = [
        attempt("0", "INFRASTRUCTURE_FAILURE", "0"),
        attempt("0", "WIN", "1"),
        attempt("0", "GAME_LOSS", "2"),
        attempt("1", "AGENT_PROTOCOL_FAILURE", "3"),
        attempt("2", "BUDGET_EXHAUSTED", "4"),
        attempt("3", "OPERATOR_ABORT", "5"),
        attempt("4", "WIN", "6", branch=True),
    ]
    r = summarize(plan, attempts)["agents"]["a"]
    assert (r["planned"], r["valid"], r["wins"], r["unresolved"]) == (5, 3, 1, 2)
    assert r["missing_outcome_bounds"] == [0.2, 0.6] and r["win_rate"] == pytest.approx(1 / 3)
    assert r["all_attempt_cost_usd"] == 6
    assert summarize(plan, [])["agents"]["a"]["win_rate"] is None


def test_AT20_bootstrap_uses_seed_clusters():
    assert cluster_interval({"a": [1, 1, 1], "b": [0, 0, 0]}) == [0.0, 1.0]
    assert cluster_interval({"a": [1, 1]}) is None
    assert cluster_interval({"a": [1, 0], "b": [1, 0]}) == [0.5, 0.5]


def test_AT21_export_injected_private_values_rejected():
    for value in [
        {"note": "/root/private/save"},
        {"data": "sk-proj-abcdefghijk123456"},
        {"seed": "secret-game-seed"},
    ]:
        with pytest.raises(ValueError):
            scan(value, ["secret-game-seed"])
    scan({"note": "<script>alert(1)</script>"})  # JSON/text consumers must escape, never execute.


def test_export_allows_public_links_but_rejects_embedded_private_paths():
    scan({"reference": "[Rules](https://example.org/rules) and http://example.org/help"})
    for value in (
        "C:/Users/example/save.dat",
        r"D:\BalatroHorizonsRuntime\private.json",
        "file:///C:/Users/example/save.dat",
        "https://example.org/?file=C:/Users/example/save.dat",
        "embedded sk-" + "synthetic-fixture-" * 4,
        "https://example.org/rules?seed=secret-game-seed",
    ):
        with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
            scan({"reference": value}, ["secret-game-seed"])
