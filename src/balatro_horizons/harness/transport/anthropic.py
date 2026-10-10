"""Claude Messages runtime, thinking mapping, and conservative accounting."""

import os

from balatro_horizons.harness.transport import anthropic_stream
from balatro_horizons.harness.transport.base import Transport, response_items, single_call
from balatro_horizons.harness.transport.claude_models import EFFORTS, HAIKU_INPUT_CEILING, MODELS
from balatro_horizons.harness.transport.conversation import (
    canonical_messages,
    encode,
    exchange_result,
    native_items,
)
from balatro_horizons.harness.transport.errors import ProtocolFailure, ProviderFailure

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


def native_messages(items, result):
    calls = [item for item in items if item.get("type") == "tool_use"]
    if not calls:
        assistant = [{"role": "assistant", "content": items}] if items else []
        return assistant + [{
            "role": "user", "content": [{"type": "text", "text": encode({"tool_error": result})}],
        }]
    output = encode(result)
    inner = result.get("result", result) if isinstance(result, dict) else result
    is_error = isinstance(inner, dict) and bool(inner.get("error"))
    results = [
        {"type": "tool_result", "tool_use_id": call["id"], "content": output}
        for call in calls
    ]
    if is_error:
        for item in results:
            item["is_error"] = True
    return [{"role": "assistant", "content": items}, {"role": "user", "content": results}]


def synthetic_messages(call, result, index):
    if not call:
        return [{"role": "user", "content": encode({"tool_error": result})}]
    call_id = f"harness_exchange_{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": call["name"],
                    "input": call["arguments"],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": encode(result),
                }
            ],
        },
    ]


def tool_messages(ctx, exchanges):
    messages = canonical_messages(ctx)
    for index, exchange in enumerate(exchanges):
        result = exchange_result(ctx, exchange)
        items = native_items(exchange, "anthropic")
        messages.extend(native_messages(items, result) if items is not None else
                        synthetic_messages(exchange.get("tool_call"), result, index))
    return messages


def payload(ctx, exchanges):
    return {
        "system": ctx.prompt + "\n\n" + ctx.rules_kernel,
        "messages": tool_messages(ctx, exchanges),
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
                "strict": False,
            }
            for tool in ctx.tools
        ],
    }


def request_body(transport, ctx, exchanges):
    settings = transport.model.settings
    model = transport.model.model
    validate_config(transport.model, transport.limits)
    body = {
        "model": model,
        **payload(ctx, exchanges),
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "max_tokens": transport.limits.max_output_tokens_per_call,
        "service_tier": "standard_only",
        "stream": True,
    }
    if model in MODELS:
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
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


def validate_config(model, limits):
    if model.model == "claude-haiku-5-5" and limits.max_input_tokens_per_call > HAIKU_INPUT_CEILING:
        raise ProviderFailure("CLAUDE_LONG_CONTEXT_PRICING_NOT_CONFIGURED")
    if "thinking_budget" in model.settings:
        budget = model.settings["thinking_budget"]
        if type(budget) is not int or budget < 1024:
            raise ProviderFailure("INVALID_THINKING_BUDGET")
        if budget >= limits.max_output_tokens_per_call:
            raise ProviderFailure("THINKING_BUDGET_MUST_BE_BELOW_OUTPUT_LIMIT")


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


class ClaudeMessagesRuntime(Transport):
    endpoint = "https://api.anthropic.com/v1/messages"
    count_endpoint = endpoint + "/count_tokens"
    key_name = "ANTHROPIC_API_KEY"
    headers = staticmethod(headers)
    request_body = request_body
    tool_messages = staticmethod(tool_messages)
    assemble = staticmethod(anthropic_stream.assemble)

    @staticmethod
    def count_payload(body):
        from copy import deepcopy

        fields = ("model", "messages", "system", "tools", "tool_choice", "thinking", "output_config")
        return deepcopy({key: body[key] for key in fields if key in body})

    def usage_cost(self, response, reserved):
        return usage_cost(self.model, response, reserved)

    def usage_known(self, response):
        return usage_cost(self.model, response, -1) >= 0

    def parse(self, response):
        self.last_tool_call = self.last_provider_turn = None
        items = response_items(response, "content")
        reason = response.get("stop_reason")
        self._terminal(reason)
        calls = self.capture_turn(items, "tool_use", "id")
        if reason == "refusal":
            raise ProtocolFailure("PROVIDER_REFUSAL", provider_stop_reason=reason)
        if not calls and reason != "tool_use":
            raise ProtocolFailure("NO_OPERATION", provider_stop_reason=reason)
        if calls and reason != "tool_use":
            raise ProtocolFailure("UNEXPECTED_PROVIDER_STOP", provider_stop_reason=reason)
        call = single_call(calls)
        return self.decode_call(call.get("name"), call.get("input"))

    def _terminal(self, reason):
        if reason in ("tool_use", "end_turn", "stop_sequence", "refusal"):
            return
        if reason == "max_tokens":
            code = "PROVIDER_RESPONSE_INCOMPLETE"
        elif reason == "model_context_window_exceeded":
            code = "PROVIDER_CONTEXT_LIMIT"
        elif reason == "pause_turn":
            code = "PROVIDER_RESPONSE_PAUSED"
        else:
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        raise ProtocolFailure(code, provider_stop_reason=reason)
