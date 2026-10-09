"""Anthropic Messages field spec, thinking mapping, and conservative accounting."""

import json
import os

from balatro_horizons.harness.transport.base import (
    ProviderFailure,
    ProviderSpec,
    encode,
    tool_messages,
)
from balatro_horizons.harness.transport.claude_models import EFFORTS, HAIKU_INPUT_CEILING, MODELS

CAPABILITIES = {
    "prompt_cache_diagnostics": lambda model: model in MODELS,
    "explicit_cache_mode": lambda model: model in MODELS,
    "unsupported_settings": lambda _model: {},
    "reasoning_efforts": lambda model, _settings: EFFORTS if model in MODELS else (),
    "supported_settings": frozenset({"temperature", "thinking_budget", "reasoning_effort"}),
}


def headers(key):
    values = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if workspace := os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        values["anthropic-workspace-id"] = workspace
    return values


def public_capabilities(model, settings):
    current = model in MODELS
    return {
        "display_name": MODELS[model][0] if current else model,
        "prompt_cache_diagnostics": current,
        "explicit_cache_mode": current,
        "supported_settings": ["reasoning_effort"] if current else ["temperature", "thinking_budget"],
        "unsupported_settings": {},
        "reasoning_efforts": list(EFFORTS) if current else [],
        "default_reasoning_effort": MODELS[model][1] if current else None,
    }


def validate_settings(model, settings):
    if model not in MODELS:
        if "reasoning_effort" in settings:
            raise ValueError("reasoning effort requires a declared adaptive-thinking Claude model")
        return
    if "temperature" in settings or "thinking_budget" in settings:
        raise ValueError(f"{model} uses adaptive thinking, not temperature or thinking_budget")
    if "reasoning_effort" in settings and settings["reasoning_effort"] not in EFFORTS:
        raise ValueError(f"{model} requires low, medium, high, xhigh or max reasoning effort")


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
    model = transport.model.model
    if model == "claude-haiku-5-5" and (
        transport.limits.max_input_tokens_per_call > HAIKU_INPUT_CEILING
    ):
        raise ProviderFailure("CLAUDE_LONG_CONTEXT_PRICING_NOT_CONFIGURED")
    body = {
        "model": model,
        **payload(ctx, exchanges),
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "max_tokens": transport.limits.max_output_tokens_per_call,
        "service_tier": "standard_only",
    }
    if model in MODELS:
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": settings.get("reasoning_effort", MODELS[model][1])}
    if "thinking_budget" in settings:
        if settings["thinking_budget"] >= body["max_tokens"]:
            raise ProviderFailure("THINKING_BUDGET_MUST_BE_BELOW_OUTPUT_LIMIT")
        body["thinking"] = {"type": "enabled", "budget_tokens": settings["thinking_budget"]}
    if "temperature" in settings:
        body["temperature"] = settings["temperature"]
    if transport.model.cache_write_input_usd_per_million is not None:
        # Cache tools + the frozen system prefix, never per-decision observations.
        body["system"] = [{
            "type": "text",
            "text": body["system"],
            "cache_control": {"type": "ephemeral", "ttl": "5m"},
        }]
    return body


def usage_cost(model, response, reserved):
    usage = response.get("usage") if isinstance(response, dict) else None
    totals = _usage_totals(usage)
    if totals is None:
        return reserved
    ordinary, output, cached, writes = totals
    if model.model == "claude-haiku-5-5" and ordinary + cached + writes > HAIKU_INPUT_CEILING:
        return reserved  # This release does not configure Haiku's long-context tier.
    if model.cached_input_usd_per_million is None:
        if writes:
            return reserved
        return ((ordinary + cached) * model.input_usd_per_million
                + output * model.output_usd_per_million) / 1_000_000
    return (
        ordinary * model.input_usd_per_million
        + cached * model.cached_input_usd_per_million
        + writes * model.cache_write_input_usd_per_million
        + output * model.output_usd_per_million
    ) / 1_000_000


def _usage_totals(usage):
    if not isinstance(usage, dict):
        return None
    totals = (
        usage.get("input_tokens"), usage.get("output_tokens"),
        usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0),
    )
    if any(type(value) is not int or value < 0 for value in totals):
        return None
    if usage.get("service_tier", "standard") != "standard":
        return None
    if usage.get("inference_geo", "global") != "global":
        return None
    detail = usage.get("cache_creation")
    if detail is not None:
        if not isinstance(detail, dict):
            return None
        short, long = detail.get("ephemeral_5m_input_tokens"), detail.get("ephemeral_1h_input_tokens")
        # Only five-minute writes are configured. Do not guess the price of another TTL.
        if type(short) is not int or short != totals[3] or type(long) is not int or long != 0:
            return None
    return totals


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
    clear_terminals=frozenset({"max_tokens", "model_context_window_exceeded", "pause_turn"}),
    endpoint="https://api.anthropic.com/v1/messages",
    key_name="ANTHROPIC_API_KEY",
    headers=headers,
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
