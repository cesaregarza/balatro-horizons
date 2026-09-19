import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project
from test_harness_tools import config_for

from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.runner import Runner


def model(provider):
    return ModelConfig(
        provider=provider,
        model="gpt-5.6-terra" if provider == "openai" else "claude-test",
        input_usd_per_million=2,
        output_usd_per_million=12,
        cached_input_usd_per_million=0.2 if provider == "openai" else None,
        cache_write_input_usd_per_million=2.5 if provider == "openai" else None,
        pricing_date="2026-09-16",
        settings={},
    )


def initial(provider):
    observation = project(FakeGame().observe_private())
    ctx, exchanges = decision_context(observation, [])
    policy = DirectProvider(model(provider), Limits())
    return observation, ctx, policy, policy.request(ctx, exchanges)


def exchange(policy, operation, result):
    return {
        "operation": operation,
        "result": result,
        "tool_call": deepcopy(policy.last_tool_call),
        "provider_turn": deepcopy(policy.last_provider_turn),
    }


def test_openai_stateless_reasoning_and_call_are_round_tripped_exactly():
    observation, ctx, policy, first = initial("openai")
    assert first["include"] == ["reasoning.encrypted_content"]
    assert first["store"] is False and first["tool_choice"] == "auto"
    assert "buy" in {tool["name"] for tool in first["tools"]}
    assert "buy" not in policy.available_tools
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "inspect first"}],
        "encrypted_content": "opaque-openai-state",
    }
    call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "status": "completed",
        "name": "inspect_state",
        "arguments": '{"section":"hand","offset":0}',
    }
    operation = policy.parse({"status": "completed", "output": [reasoning, call]})
    result = {"content": "[]", "complete": True, "game_advanced": False}
    ctx, delivered = decision_context(
        observation, [exchange(policy, operation, result)]
    )
    followup = policy.request(ctx, delivered)
    assert reasoning in followup["input"] and call in followup["input"]
    output = next(item for item in followup["input"] if item.get("type") == "function_call_output")
    assert output == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
    }
    assert json.dumps(followup).count("opaque-openai-state") == 1


def test_anthropic_thinking_redaction_text_and_call_are_round_tripped_exactly():
    observation, ctx, policy, first = initial("anthropic")
    assert first["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(tool["strict"] is True for tool in first["tools"])
    blocks = [
        {"type": "thinking", "thinking": "inspect first", "signature": "opaque-signature"},
        {"type": "redacted_thinking", "data": "opaque-redacted-state"},
        {"type": "text", "text": "I will inspect the hand."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "inspect_state",
            "input": {"section": "hand", "offset": 0},
        },
    ]
    operation = policy.parse({"stop_reason": "tool_use", "content": deepcopy(blocks)})
    result = {"content": "[]", "complete": True, "game_advanced": False}
    ctx, delivered = decision_context(
        observation, [exchange(policy, operation, result)]
    )
    followup = policy.request(ctx, delivered)
    assistant = next(message for message in followup["messages"] if message["role"] == "assistant")
    assert assistant["content"] == blocks
    tool_result = followup["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1"
    assert "is_error" not in tool_result


def test_cache_transport_and_accounting_remain_conservative():
    _, _, openai, openai_body = initial("openai")
    assert openai_body["prompt_cache_options"] == {"mode": "explicit"}
    usage = {
        "usage": {
            "input_tokens": 5000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 1000},
        }
    }
    assert openai.usage_cost(usage, 1) == pytest.approx(
        (1000 * 2 + 3000 * 0.2 + 1000 * 2.5 + 100 * 12) / 1_000_000
    )
    _, _, anthropic, anthropic_body = initial("anthropic")
    assert "cache_control" not in json.dumps(anthropic_body)
    assert (
        anthropic.usage_cost(
            {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "cache_creation_input_tokens": 1,
                }
            },
            0.25,
        )
        == 0.25
    )


@pytest.mark.parametrize(
    ("provider", "response", "code"),
    [
        ("openai", {"status": "completed", "output": []}, "NO_OPERATION"),
        (
            "openai",
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            },
            "PROVIDER_RESPONSE_INCOMPLETE",
        ),
        ("anthropic", {"stop_reason": "end_turn", "content": []}, "NO_OPERATION"),
        (
            "anthropic",
            {"stop_reason": "max_tokens", "content": []},
            "PROVIDER_RESPONSE_INCOMPLETE",
        ),
        (
            "anthropic",
            {"stop_reason": "refusal", "content": []},
            "PROVIDER_REFUSAL",
        ),
    ],
)
def test_stop_and_completion_failures_have_specific_codes(provider, response, code):
    _, _, policy, _ = initial(provider)
    with pytest.raises(ProtocolFailure) as error:
        policy.parse(response)
    assert error.value.code == code


def test_multiple_openai_calls_are_all_resolved_with_specific_feedback():
    observation, _, policy, _ = initial("openai")
    calls = [
        {
            "type": "function_call",
            "call_id": f"call_{index}",
            "status": "completed",
            "name": "calculate",
            "arguments": '{"expression":"1+1"}',
        }
        for index in (1, 2)
    ]
    with pytest.raises(ProtocolFailure) as error:
        policy.parse({"status": "completed", "output": calls})
    assert error.value.code == "MULTIPLE_OPERATIONS"
    feedback = Runner(None, config_for("openai"), None, policy)._tool_feedback(
        error.value, error.value.code, observation
    )
    ctx, delivered = decision_context(
        observation,
        [exchange(policy, {"kind": "invalid"}, feedback)],
    )
    followup = policy.request(ctx, delivered)
    outputs = [item for item in followup["input"] if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in outputs] == ["call_1", "call_2"]
    assert all("MULTIPLE_OPERATIONS" in item["output"] for item in outputs)


def test_invalid_openai_arguments_get_linked_json_feedback():
    observation, _, policy, _ = initial("openai")
    call = {
        "type": "function_call",
        "call_id": "call_bad_json",
        "status": "completed",
        "name": "calculate",
        "arguments": "{not-json",
    }
    with pytest.raises(ProtocolFailure) as error:
        policy.parse({"status": "completed", "output": [call]})
    assert error.value.code == "INVALID_OPERATION_JSON"
    feedback = Runner(None, config_for("openai"), None, policy)._tool_feedback(
        error.value, error.value.code, observation
    )
    ctx, delivered = decision_context(
        observation,
        [exchange(policy, {"kind": "invalid"}, feedback)],
    )
    followup = policy.request(ctx, delivered)
    result = next(item for item in followup["input"] if item.get("type") == "function_call_output")
    assert result["call_id"] == "call_bad_json"
    assert "INVALID_OPERATION_JSON" in result["output"]


def test_unavailable_anthropic_call_gets_linked_error_feedback():
    observation, _, policy, _ = initial("anthropic")
    call = {"type": "tool_use", "id": "toolu_bad", "name": "buy", "input": {}}
    with pytest.raises(ProtocolFailure) as error:
        policy.parse({"stop_reason": "tool_use", "content": [call]})
    assert error.value.code == "UNAVAILABLE_TOOL"
    feedback = Runner(None, config_for("anthropic"), None, policy)._tool_feedback(
        error.value, error.value.code, observation
    )
    ctx, delivered = decision_context(
        observation,
        [exchange(policy, {"kind": "invalid"}, feedback)],
    )
    followup = policy.request(ctx, delivered)
    result = followup["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "toolu_bad" and result["is_error"] is True
    assert "UNAVAILABLE_TOOL" in result["content"]


def test_provider_continuation_resets_after_committed_game_action(store, monkeypatch):
    config = config_for("openai")
    config.models["luna"] = model("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    requests = []
    runner = None

    def receive(request):
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests)
        if index == 3:
            assert "decision-one-reasoning" not in json.dumps(body)
            name, arguments = "abort_run", {"reason": "boundary checked"}
        elif index == 2:
            blind = runner.observation.state.revealed_blinds[0]
            name = "select_blind"
            arguments = {
                "observation_id": runner.observation.observation_id,
                "blind_id": blind.id,
                "decision_note": None,
                "note_update": None,
            }
        else:
            name, arguments = "inspect_state", {"section": "hand", "offset": 0}
        marker = "decision-one-reasoning" if index < 3 else "decision-two-reasoning"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning", "encrypted_content": marker, "summary": []},
                    {
                        "type": "function_call",
                        "call_id": f"call_{index}",
                        "status": "completed",
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    runner = Runner(store, config, FakeGame(), policy)
    result = runner.run()
    assert result["reason"] == "AGENT_ABORT"
    assert len(requests) == 3
    assert "decision-one-reasoning" in json.dumps(requests[1])
    assert "decision-one-reasoning" not in json.dumps(requests[2])


def test_context_clears_results_but_retains_provider_protocol_shells():
    observation = project(FakeGame().observe_private())
    exchanges = []
    for index in range(5):
        operation = {"kind": "arithmetic", "expression": f"{index}+1"}
        exchanges.append(
            {
                "operation": operation,
                "result": {"result": str(index + 1)},
                "tool_call": {"name": "calculate", "arguments": {"expression": f"{index}+1"}},
                "provider_turn": {
                    "version": "provider_turn_v1",
                    "provider": "openai",
                    "items": [
                        {
                            "type": "function_call",
                            "call_id": f"call_{index}",
                            "status": "completed",
                            "name": "calculate",
                            "arguments": json.dumps({"expression": f"{index}+1"}),
                        }
                    ],
                },
            }
        )
    ctx, delivered = decision_context(observation, exchanges)
    assert len(delivered) == 5
    assert [item["result"].get("context_cleared", False) for item in delivered] == [
        True,
        True,
        False,
        False,
        False,
    ]
    assert all(
        item["provider_turn"] == exchanges[i]["provider_turn"] for i, item in enumerate(delivered)
    )
    assert ctx["context_delivery"]["loaded_exchange_indices"] == [2, 3, 4]


def test_public_export_omits_opaque_provider_continuation(store):
    eid = store.create(
        {
            "schema_version": "1",
            "evidence_kind": "SYNTHETIC_TEST",
            "agent": "mock",
            "config": {},
            "evaluation_eligible": False,
        },
        {"seed": "PRIVATE-SEED"},
    )
    store.append(
        eid,
        "provider_response",
        {
            "body": {
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "public summary"}],
                        "encrypted_content": "OPENAI-OPAQUE",
                    },
                    {"type": "thinking", "thinking": "public thinking", "signature": "SIGNATURE"},
                    {"type": "redacted_thinking", "data": "REDACTED-DATA"},
                ]
            }
        },
    )
    exported = episode_export(store, eid)
    serialized = json.dumps(exported)
    assert "OPENAI-OPAQUE" not in serialized
    assert "SIGNATURE" not in serialized
    assert "REDACTED-DATA" not in serialized
    assert "public summary" in serialized and "public thinking" in serialized
    assert serialized.count("opaque_continuation_omitted") == 3
