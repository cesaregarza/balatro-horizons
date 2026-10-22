"""OpenAI Responses runtime, explicit-cache interlocks, and cost categories."""

import re

from balatro_horizons.harness.transport import openai_stream
from balatro_horizons.harness.transport.base import Transport, response_items, single_call
from balatro_horizons.harness.transport.conversation import (
    canonical_messages,
    encode,
    exchange_result,
    native_items,
)
from balatro_horizons.harness.transport.errors import (
    ProtocolFailure,
    ProviderFailure,
)
from balatro_horizons.harness.transport.sse import strict_json

MODEL_VERSION = re.compile(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)")
DISPLAY_MODEL = re.compile(r"^gpt-(5\.6|6)-(luna|terra|sol|astra)$")
GPT6_SOL_LUNA = frozenset({"gpt-6-sol", "gpt-6-luna"})


def prompt_cache_diagnostics(model):
    # Cache comparisons affect billed token categories; unknown versions fail closed.
    match = MODEL_VERSION.match(model)
    return bool(match and (int(match[1]), int(match[2] or 0)) >= (5, 6))


def explicit_cache_mode(model):
    # Explicit mode prevents silent cache-billing changes on supported model generations.
    return prompt_cache_diagnostics(model)


def unsupported_settings(model):
    # Reject unsupported efforts before a paid request.
    if model == "gpt-5.6-luna" or model in GPT6_SOL_LUNA:
        return {"reasoning_effort": {"minimal"}}
    return {}


def validate_settings(model, settings):
    if (
        model in GPT6_SOL_LUNA
        and "temperature" in settings
        and settings.get("reasoning_effort", "medium") != "none"
    ):
        raise ValueError(f"{model} supports temperature only with reasoning_effort none")


def reasoning_efforts(model, pinned):
    if model in GPT6_SOL_LUNA or re.match(r"^gpt-5\.6(?:-|$)", model):
        return ("none", "low", "medium", "high", "xhigh", "max")
    if re.match(r"^gpt-6-astra(?:-|$)", model):
        return ("low", "medium", "high", "xhigh", "max")
    value = pinned.get("reasoning_effort")
    return (str(value),) if value else ()


def display_name(model):
    match = DISPLAY_MODEL.fullmatch(model)
    if not match:
        return model
    generation, tier = match.groups()
    return f"GPT-{generation} {tier.title()}"


CAPABILITIES = {
    "prompt_cache_diagnostics": prompt_cache_diagnostics,
    "explicit_cache_mode": explicit_cache_mode,
    "unsupported_settings": unsupported_settings,
    "reasoning_efforts": reasoning_efforts,
    "supported_settings": frozenset({"temperature", "reasoning_effort", "reasoning_summary"}),
}


def public_capabilities(model, settings):
    return {
        "display_name": display_name(model),
        "prompt_cache_diagnostics": prompt_cache_diagnostics(model),
        "explicit_cache_mode": explicit_cache_mode(model),
        "supported_settings": sorted(CAPABILITIES["supported_settings"]),
        "unsupported_settings": {
            key: sorted(values) for key, values in unsupported_settings(model).items()
        },
        "reasoning_efforts": list(reasoning_efforts(model, settings)),
    }


def native_messages(items, result):
    calls = [item for item in items if item.get("type") == "function_call"]
    if not calls:
        return items + [{"role": "user", "content": encode({"tool_error": result})}]
    output = encode(result)
    return items + [
        {"type": "function_call_output", "call_id": call["call_id"], "output": output}
        for call in calls
    ]


def synthetic_messages(call, result, index):
    if not call:
        return [{"role": "user", "content": encode({"tool_error": result})}]
    call_id = f"harness_exchange_{index}"
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": call["name"],
            "arguments": encode(call["arguments"]),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": encode(result),
        },
    ]


def tool_messages(ctx, exchanges):
    messages = canonical_messages(ctx)
    for index, exchange in enumerate(exchanges):
        result = exchange_result(ctx, exchange)
        items = native_items(exchange, "openai")
        messages.extend(native_messages(items, result) if items is not None else
                        synthetic_messages(exchange.get("tool_call"), result, index))
    return messages


def payload(ctx, exchanges):
    instructions = ctx.prompt + "\n\n" + ctx.rules_kernel
    return {
        "input": [
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": instructions,
                        "prompt_cache_breakpoint": {"mode": "explicit"},
                    }
                ],
            }
        ]
        + tool_messages(ctx, exchanges),
        "tools": [{"type": "function", **tool, "strict": True} for tool in ctx.tools],
        "tool_choice": "auto",
    }


def request_body(transport, ctx, exchanges):
    settings = transport.model.settings
    body = {
        "model": transport.model.model,
        **payload(ctx, exchanges),
        "parallel_tool_calls": False,
        "store": False,
        "service_tier": "default",
        "truncation": "disabled",
        "max_output_tokens": transport.limits.max_output_tokens_per_call,
        "stream": True,
    }
    reasoning = {"summary": "auto", **{
        name: settings[key]
        for name, key in (("effort", "reasoning_effort"), ("summary", "reasoning_summary"))
        if key in settings
    }}
    if reasoning:
        body["reasoning"] = reasoning
    body["prompt_cache_options"] = _cache_options(transport)
    # store=false is deliberate. Encrypted reasoning makes returned reasoning items
    # round-trippable during this one decision.
    body["include"] = ["reasoning.encrypted_content"]
    if "temperature" in settings:
        body["temperature"] = settings["temperature"]
    return body


def _cache_options(transport):
    validate_config(transport.model, transport.limits)
    result = {"mode": "explicit"}
    if transport._last_completed_openai_response_id:
        result["comparison_response_id"] = transport._last_completed_openai_response_id
    return result


def validate_config(model, limits):
    if not prompt_cache_diagnostics(model.model):
        raise ProviderFailure("EXPLICIT_CACHE_REQUIRES_GPT_5_6_OR_LATER")
    if model.cache_write_input_usd_per_million is None:
        raise ProviderFailure("EXPLICIT_CACHE_PRICING_REQUIRED")
    if limits.max_input_tokens_per_call > 272_000:
        raise ProviderFailure("CACHE_LONG_CONTEXT_PRICING_NOT_CONFIGURED")


def usage_cost(model, response, reserved):
    usage = response.get("usage") if isinstance(response, dict) else None
    totals = _usage_totals(usage)
    if totals is None:
        return reserved
    input_tokens, output_tokens = totals
    if model.cached_input_usd_per_million is None:
        return _uncategorized_cost(model, usage, input_tokens, output_tokens, reserved)
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        return reserved
    cached, writes = details.get("cached_tokens"), details.get("cache_write_tokens")
    if (
        type(cached) is not int
        or type(writes) is not int
        or cached < 0
        or writes < 0
        or cached + writes > input_tokens
    ):
        return reserved
    return (
        (input_tokens - cached - writes) * model.input_usd_per_million
        + cached * model.cached_input_usd_per_million
        + writes * model.cache_write_input_usd_per_million
        + output_tokens * model.output_usd_per_million
    ) / 1_000_000


def _usage_totals(usage):
    if not isinstance(usage, dict):
        return None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if (
        type(input_tokens) is not int
        or input_tokens < 0
        or type(output_tokens) is not int
        or output_tokens < 0
    ):
        return None
    return input_tokens, output_tokens


def _uncategorized_cost(model, usage, input_tokens, output_tokens, reserved):
    if usage.get("cache_creation_input_tokens"):
        return reserved
    return (
        input_tokens * model.input_usd_per_million
        + output_tokens * model.output_usd_per_million
    ) / 1_000_000


def validate_call(call):
    status = call.get("status", "completed")
    if status == "incomplete":
        raise ProtocolFailure("PROVIDER_RESPONSE_INCOMPLETE", provider_status=status)
    if status != "completed":
        raise ProtocolFailure("PROVIDER_RESPONSE_UNRESOLVED", provider_status=str(status))


def terminal_details(response, terminal):
    details = response.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    return (
        {"provider_reason": reason} if terminal == "incomplete" and isinstance(reason, str) else {}
    )


class OpenAIResponsesRuntime(Transport):
    endpoint = "https://api.openai.com/v1/responses"
    count_endpoint = endpoint + "/input_tokens"
    key_name = "OPENAI_API_KEY"

    def __init__(self, model, limits, client=None):
        super().__init__(model, limits, client)
        self._last_completed_openai_response_id = None

    @staticmethod
    def headers(key):
        return {"Authorization": "Bearer " + key}

    request_body = request_body
    tool_messages = staticmethod(tool_messages)
    assemble = staticmethod(openai_stream.assemble)

    @staticmethod
    def count_payload(body):
        from copy import deepcopy

        fields = ("model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls",
                  "text", "truncation", "previous_response_id", "conversation")
        return deepcopy({key: body[key] for key in fields if key in body})

    def usage_cost(self, response, reserved):
        return usage_cost(self.model, response, reserved)

    def usage_known(self, response):
        return usage_cost(self.model, response, -1) >= 0

    def remember_response(self, response):
        if response.get("status") == "completed" and response.get("id"):
            self._last_completed_openai_response_id = response["id"]

    def parse(self, response):
        self.last_tool_call = self.last_provider_turn = None
        items = response_items(response, "output")
        self._terminal(response)
        calls = self.capture_turn(items, "function_call", "call_id")
        if any(block.get("type") == "refusal" for item in items
               if item.get("type") == "message" and isinstance(item.get("content"), list)
               for block in item["content"] if isinstance(block, dict)):
            raise ProtocolFailure("PROVIDER_REFUSAL")
        call = single_call(calls)
        try:
            validate_call(call)
        except ProtocolFailure:
            self.last_provider_turn = None
            raise
        if not isinstance(call.get("name"), str) or call["name"] not in self.available_tools:
            raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(call.get("name")))
        try:
            arguments = strict_json(call.get("arguments"))
        except (TypeError, ValueError, RecursionError):
            raise ProtocolFailure("INVALID_OPERATION_JSON") from None
        return self.decode_call(call.get("name"), arguments)

    def _terminal(self, response):
        status = response.get("status")
        if status == "completed":
            return
        if status == "incomplete":
            code = "PROVIDER_RESPONSE_INCOMPLETE"
        elif status in ("failed", "cancelled"):
            code = "PROVIDER_RESPONSE_FAILED"
        elif status in ("in_progress", "queued"):
            code = "PROVIDER_RESPONSE_UNRESOLVED"
        else:
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        raise ProtocolFailure(code, provider_status=status, **terminal_details(response, status))
