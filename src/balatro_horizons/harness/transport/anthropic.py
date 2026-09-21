"""Anthropic Messages field spec, thinking mapping, and conservative accounting."""

import json

from balatro_horizons.harness.transport.base import ProviderSpec, encode, tool_messages

CAPABILITIES = {
    "prompt_cache_diagnostics": lambda _model: False,
    "explicit_cache_mode": lambda _model: False,
    "unsupported_settings": lambda _model: {},
    "reasoning_efforts": lambda _model, _settings: (),
    "supported_settings": frozenset({"temperature", "thinking_budget"}),
}


def public_capabilities(model, settings):
    return {
        "display_name": model,
        "prompt_cache_diagnostics": False,
        "explicit_cache_mode": False,
        "supported_settings": sorted(CAPABILITIES["supported_settings"]),
        "unsupported_settings": {},
        "reasoning_efforts": [],
    }


def native_messages(items, result, spec):
    calls = [item for item in items if item.get("type") == "tool_use"]
    if not calls:
        return [
            {"role": "assistant", "content": items},
            {"role": "user", "content": [{"type": "text", "text": encode({"tool_error": result})}]},
        ]
    output = encode(result)
    is_error = isinstance(result, dict) and bool(result.get("error"))
    results = [
        {"type": spec.result_item_type, "tool_use_id": call["id"], "content": output}
        for call in calls
    ]
    if is_error:
        for item in results:
            item["is_error"] = True
    return [{"role": "assistant", "content": items}, {"role": "user", "content": results}]


def synthetic_messages(call, result, index, spec):
    if not call:
        return [{"role": "user", "content": json.dumps({"tool_error": result})}]
    call_id = f"harness_exchange_{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": spec.call_item_type,
                    spec.id_field: call_id,
                    "name": call["name"],
                    spec.args_field: call["arguments"],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": spec.result_item_type,
                    "tool_use_id": call_id,
                    "content": encode(result),
                }
            ],
        },
    ]


def payload(ctx, exchanges):
    return {
        "system": ctx.prompt + "\n\n" + ctx.rules_kernel,
        "messages": tool_messages(ctx, exchanges, SPEC),
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
                "strict": True,
            }
            for tool in ctx.tools
        ],
    }


def request_body(transport, ctx, exchanges):
    settings = transport.model.settings
    body = {
        "model": transport.model.model,
        **payload(ctx, exchanges),
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "max_tokens": transport.limits.max_output_tokens_per_call,
    }
    if "thinking_budget" in settings:
        body["thinking"] = {"type": "enabled", "budget_tokens": settings["thinking_budget"]}
    if "temperature" in settings:
        body["temperature"] = settings["temperature"]
    return body


def usage_cost(model, response, reserved):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return reserved
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if (
        type(input_tokens) is not int
        or input_tokens < 0
        or type(output_tokens) is not int
        or output_tokens < 0
    ):
        return reserved
    # Cache creation can be priced higher. Until explicit write pricing is configured,
    # retain the full reservation whenever creation tokens are reported.
    if usage.get("cache_creation_input_tokens"):
        return reserved
    cached = usage.get("cache_read_input_tokens", 0)
    if type(cached) is not int or cached < 0:
        return reserved
    return (
        (input_tokens + cached) * model.input_usd_per_million
        + output_tokens * model.output_usd_per_million
    ) / 1_000_000


SPEC = ProviderSpec(
    name="anthropic",
    terminal_field="stop_reason",
    terminal_map={
        "max_tokens": "PROVIDER_RESPONSE_INCOMPLETE",
        "model_context_window_exceeded": "PROVIDER_CONTEXT_LIMIT",
        "refusal": "PROVIDER_REFUSAL",
        "pause_turn": "PROVIDER_RESPONSE_PAUSED",
    },
    call_item_type="tool_use",
    id_field="id",
    args_field="input",
    result_item_type="tool_result",
    cache_options=None,
    items_field="content",
    default_terminal=None,
    success_terminals=frozenset({None, "tool_use"}),
    terminal_detail="provider_stop_reason",
    endpoint="https://api.anthropic.com/v1/messages",
    key_name="ANTHROPIC_API_KEY",
    headers=lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    payload=payload,
    request_body=request_body,
    usage_cost=usage_cost,
    decode_arguments=lambda value: value,
    validate_call=lambda _call: None,
    terminal_details=lambda _response, _terminal: {},
    native_messages=native_messages,
    synthetic_messages=synthetic_messages,
    defer_unknown_terminal=True,
)
