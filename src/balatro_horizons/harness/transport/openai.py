"""OpenAI Responses field spec, explicit-cache interlocks, and cost categories."""

import json
import re

from balatro_horizons.harness.transport.base import (
    ProtocolFailure,
    ProviderFailure,
    ProviderSpec,
    encode,
    tool_messages,
)

MODEL_VERSION = re.compile(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)")
DISPLAY_MODEL = re.compile(r"^gpt-(5\.6|6)-(luna|terra|sol|astra)$")


def prompt_cache_diagnostics(model):
    # Cache comparisons affect billed token categories; unknown versions fail closed.
    match = MODEL_VERSION.match(model)
    return bool(match and (int(match[1]), int(match[2] or 0)) >= (5, 6))


def explicit_cache_mode(model):
    # Explicit mode prevents silent cache-billing changes on supported model generations.
    return prompt_cache_diagnostics(model)


def unsupported_settings(model):
    # Luna rejects minimal effort upstream; declaring it prevents paid rejected requests.
    return {"reasoning_effort": {"minimal"}} if model == "gpt-5.6-luna" else {}


def reasoning_efforts(model, pinned):
    if re.match(r"^gpt-5\.6(?:-|$)", model):
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


def native_messages(items, result, spec):
    calls = [item for item in items if item.get("type") == "function_call"]
    if not calls:
        return items + [{"role": "user", "content": encode({"tool_error": result})}]
    output = encode(result)
    return items + [
        {"type": spec.result_item_type, "call_id": call["call_id"], "output": output}
        for call in calls
    ]


def synthetic_messages(call, result, index, spec):
    if not call:
        return [{"role": "user", "content": json.dumps({"tool_error": result})}]
    call_id = f"harness_exchange_{index}"
    return [
        {
            "type": spec.call_item_type,
            spec.id_field: call_id,
            "name": call["name"],
            spec.args_field: encode(call["arguments"]),
        },
        {
            "type": spec.result_item_type,
            spec.id_field: call_id,
            "output": encode(result),
        },
    ]


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
        + tool_messages(ctx, exchanges, SPEC),
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
    }
    reasoning = {
        name: settings[key]
        for name, key in (("effort", "reasoning_effort"), ("summary", "reasoning_summary"))
        if key in settings
    }
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
    if not prompt_cache_diagnostics(transport.model.model):
        raise ProviderFailure("EXPLICIT_CACHE_REQUIRES_GPT_5_6_OR_LATER")
    if transport.model.cache_write_input_usd_per_million is None:
        raise ProviderFailure("EXPLICIT_CACHE_PRICING_REQUIRED")
    if transport.limits.max_input_tokens_per_call > 272_000:
        raise ProviderFailure("CACHE_LONG_CONTEXT_PRICING_NOT_CONFIGURED")
    result = dict(SPEC.cache_options)
    if transport._last_completed_openai_response_id:
        result["comparison_response_id"] = transport._last_completed_openai_response_id
    return result


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
    cached = usage.get("cache_read_input_tokens", 0)
    if type(cached) is not int or cached < 0:
        return reserved
    return (
        (input_tokens + cached) * model.input_usd_per_million
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


SPEC = ProviderSpec(
    name="openai",
    terminal_field="status",
    terminal_map={
        "incomplete": "PROVIDER_RESPONSE_INCOMPLETE",
        "failed": "PROVIDER_RESPONSE_FAILED",
        "cancelled": "PROVIDER_RESPONSE_FAILED",
        "in_progress": "PROVIDER_RESPONSE_UNRESOLVED",
        "queued": "PROVIDER_RESPONSE_UNRESOLVED",
    },
    call_item_type="function_call",
    id_field="call_id",
    args_field="arguments",
    result_item_type="function_call_output",
    cache_options={"mode": "explicit"},
    items_field="output",
    default_terminal="completed",
    success_terminals=frozenset({"completed"}),
    terminal_detail="provider_status",
    clear_terminals=frozenset({"incomplete", "failed", "cancelled", "in_progress", "queued"}),
    endpoint="https://api.openai.com/v1/responses",
    key_name="OPENAI_API_KEY",
    headers=lambda key: {"Authorization": "Bearer " + key},
    payload=payload,
    request_body=request_body,
    usage_cost=usage_cost,
    decode_arguments=json.loads,
    validate_call=validate_call,
    terminal_details=terminal_details,
    native_messages=native_messages,
    synthetic_messages=synthetic_messages,
    response_id_field="id",
    completed_terminal="completed",
)
