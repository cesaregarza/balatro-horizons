import json
import uuid

import pytest
from test_boundary import project

from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.evaluation.batches import cluster_interval, summarize
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure


def model(provider):
    return ModelConfig(
        provider=provider,
        model="gpt-5.6-test" if provider == "openai" else "claude-test",
        input_usd_per_million=1,
        output_usd_per_million=2,
        cached_input_usd_per_million=0.1 if provider == "openai" else None,
        cache_write_input_usd_per_million=1.25 if provider == "openai" else None,
        pricing_date="2026-09-14",
    )


def test_AT15_provider_parity():
    ctx = context(project(FakeGame().observe_private()))
    policies = [DirectProvider(model(provider), Limits()) for provider in ("openai", "anthropic")]
    try:
        a, b = [policy.request(ctx, []) for policy in policies]
        assert a["input"][1:] == b["messages"]
        assert a["input"][0]["content"][0]["text"] == b["system"]
        assert [(tool["name"], tool["parameters"]) for tool in a["tools"]] == [
            (tool["name"], tool["input_schema"]) for tool in b["tools"]
        ]
        assert a["parallel_tool_calls"] is False
        assert b["tool_choice"]["disable_parallel_tool_use"] is True
        assert "api_key" not in json.dumps(a) + json.dumps(b)
    finally:
        for policy in policies:
            policy.client.close()


def test_AT15_provider_rejects_multiple_operations():
    ctx = context(project(FakeGame().observe_private()))
    p = DirectProvider(model("openai"), Limits())
    p.request(ctx, [])
    with pytest.raises(ProtocolFailure, match="MULTIPLE_OPERATIONS"):
        p.parse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_{index}",
                        "name": "calculate",
                        "arguments": '{"expression":"1+1"}',
                    }
                    for index in range(2)
                ],
            }
        )
    p.client.close()
    p = DirectProvider(model("anthropic"), Limits())
    p.request(ctx, [])
    assert p.parse(
        {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_abort",
                    "name": "abort_run",
                    "input": {"reason": "test"},
                }
            ],
        }
    )["kind"] == "abort"
    p.client.close()


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
    from balatro_horizons.evaluation.batches import cluster_diagnostic
    assert cluster_interval({"a": [1, 1, 1], "b": [0, 0, 0]}) == [0.0, 1.0]
    assert cluster_interval({"a": [1, 1]}) is None
    assert cluster_interval({"a": [1, 0], "b": [1, 0]}) == [0.5, 0.5]
    assert cluster_diagnostic({"a": [0], "b": [0]})["status"] == "degenerate"
    assert cluster_diagnostic({"a": [1], "b": [1]})["status"] == "degenerate"
    assert cluster_diagnostic({"a": [1], "b": [0]})["status"] == "varying_seed_means"
    assert cluster_diagnostic({"a": [1]})["status"] == "insufficient_seed_clusters"


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
