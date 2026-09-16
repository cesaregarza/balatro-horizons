"""Direct transport adapters; canonical context and tool behavior live elsewhere."""

import json
import os
import re

import httpx

from balatro_horizons.agents.protocol import TOOL
from balatro_horizons.agents.tool_interface import FOCUSED_INTERFACES, NAMED_INTERFACES, decode_tool


class ProviderFailure(RuntimeError):
    def __init__(self, code, retryable=False, provider_code=None):
        self.code, self.retryable = code, retryable
        self.provider_code = provider_code
        super().__init__(code)


class ProtocolFailure(ValueError):
    pass


def encode(value, ctx):
    separators = (",", ":") if ctx.get("interface_version") in FOCUSED_INTERFACES else None
    return json.dumps(value, ensure_ascii=False, separators=separators)


def canonical_messages(ctx, exchanges):
    messages = [
        {
            "role": "user",
            "content": encode(
                {"observation": ctx["observation"], "omitted_event_ids": ctx["omitted_event_ids"]},
                ctx,
            ),
        }
    ]
    for exchange in exchanges:
        messages.append(
            {"role": "assistant", "content": json.dumps(exchange["operation"], ensure_ascii=False)}
        )
        messages.append(
            {
                "role": "user",
                "content": json.dumps({"operation_result": exchange["result"]}, ensure_ascii=False),
            }
        )
    return messages


def tool_messages(ctx, exchanges, provider):
    """Reconstruct public tool exchanges, without provider-owned hidden memory."""
    messages = canonical_messages(ctx, [])
    for index, exchange in enumerate(exchanges):
        call = exchange.get("tool_call")
        if not call:
            messages.append(
                {"role": "user", "content": json.dumps({"tool_error": exchange["result"]})}
            )
            continue
        call_id = f"harness_exchange_{index}"
        output = encode(exchange["result"], ctx)
        if provider == "openai":
            messages.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": call["name"],
                        "arguments": encode(call["arguments"], ctx),
                    },
                    {"type": "function_call_output", "call_id": call_id, "output": output},
                ]
            )
        else:
            messages.extend(
                [
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
                            {"type": "tool_result", "tool_use_id": call_id, "content": output}
                        ],
                    },
                ]
            )
    return messages


def context_payload(ctx, exchanges, provider, interface):
    """One serializer shared by budget planning and the actual provider request."""
    messages = (
        tool_messages(ctx, exchanges, provider)
        if interface in NAMED_INTERFACES
        else canonical_messages(ctx, exchanges)
    )
    instructions = ctx["prompt"] + "\n\n" + ctx["rules_kernel"]
    definitions = ctx["tools"] if interface in NAMED_INTERFACES else [TOOL]
    if provider == "openai":
        if interface == "tools_v4":
            # A stable developer block follows the fixed tool catalog. The explicit
            # write ends here: observations and retrieved pages are not cached.
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
                + messages,
                "tools": [{"type": "function", **t, "strict": True} for t in definitions],
                "tool_choice": {
                    "type": "allowed_tools",
                    "mode": "auto",
                    "tools": [{"type": "function", "name": name} for name in ctx["allowed_tools"]],
                },
            }
        return {
            "instructions": instructions,
            "input": messages,
            "tools": [
                {"type": "function", **t, "strict": interface in NAMED_INTERFACES}
                for t in definitions
            ],
        }
    return {
        "system": instructions,
        "messages": messages,
        "tools": [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in definitions
        ],
    }


class DirectProvider:
    paid = True

    def __init__(self, model, limits, client=None):
        self.model, self.limits = model, limits
        self.client = client or httpx.Client(timeout=90, trust_env=False)
        self.key_name = "OPENAI_API_KEY" if model.provider == "openai" else "ANTHROPIC_API_KEY"
        self.last_request = None
        self.last_response = None
        self.interface = model.settings.get("harness_interface", "operate_v1")
        self.available_tools = {"operate"}
        self.last_tool_call = None
        # Diagnostic comparison state is deliberately instance-local. RunService creates
        # a fresh provider for each run, so response IDs cannot cross episode lifecycles.
        self._last_completed_openai_response_id = None

    def _supports_prompt_cache_diagnostics(self):
        """Conservatively recognize documented GPT-5.6+ model identifiers."""
        if self.model.provider != "openai":
            return False
        match = re.match(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)", self.model.model)
        return bool(match and (int(match[1]), int(match[2] or 0)) >= (5, 6))

    def _remember_completed_response(self, response):
        if self.model.provider != "openai" or not isinstance(response, dict):
            return
        response_id = response.get("id")
        # Missing status is not evidence of completion. Keep the last known completed
        # baseline across incomplete, failed, malformed, and transport-failed responses.
        if response.get("status") == "completed" and isinstance(response_id, str) and response_id:
            self._last_completed_openai_response_id = response_id

    def request(self, ctx, exchanges):
        settings = self.model.settings
        definitions = ctx["tools"] if self.interface in NAMED_INTERFACES else [TOOL]
        payload = context_payload(ctx, exchanges, self.model.provider, self.interface)
        self.available_tools = (
            set(ctx["allowed_tools"])
            if self.interface == "tools_v4"
            else {tool["name"] for tool in definitions}
        )
        self.last_tool_call = None
        if self.model.provider == "openai":
            body = {
                "model": self.model.model,
                **payload,
                "parallel_tool_calls": False,
                "store": False,
                "service_tier": "default",
                "truncation": "disabled",
                "max_output_tokens": self.limits.max_output_tokens_per_call,
            }
            reasoning = {
                name: settings[setting]
                for name, setting in (
                    ("effort", "reasoning_effort"),
                    ("summary", "reasoning_summary"),
                )
                if setting in settings
            }
            if reasoning:
                body["reasoning"] = reasoning
            prompt_cache_options = {}
            if self.interface == "tools_v4":
                if not self._supports_prompt_cache_diagnostics():
                    raise ProviderFailure("EXPLICIT_CACHE_REQUIRES_GPT_5_6_OR_LATER")
                if self.model.cache_write_input_usd_per_million is None:
                    raise ProviderFailure("EXPLICIT_CACHE_PRICING_REQUIRED")
                if self.limits.max_input_tokens_per_call > 272_000:
                    raise ProviderFailure("CACHE_LONG_CONTEXT_PRICING_NOT_CONFIGURED")
                prompt_cache_options["mode"] = "explicit"
            elif self.model.model == "gpt-5.6-luna":
                # Explicit mode without breakpoints disables cache reads/writes.
                # Keep this first integration on the recorded standard input rate.
                prompt_cache_options["mode"] = "explicit"
                if self.limits.max_input_tokens_per_call > 272_000:
                    raise ProviderFailure("LUNA_LONG_CONTEXT_PRICING_NOT_CONFIGURED")
            if (
                self._supports_prompt_cache_diagnostics()
                and self._last_completed_openai_response_id
            ):
                prompt_cache_options["comparison_response_id"] = (
                    self._last_completed_openai_response_id
                )
            if prompt_cache_options:
                body["prompt_cache_options"] = prompt_cache_options
        else:
            body = {
                "model": self.model.model,
                **payload,
                "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
                "max_tokens": self.limits.max_output_tokens_per_call,
            }
            if "thinking_budget" in settings:
                body["thinking"] = {"type": "enabled", "budget_tokens": settings["thinking_budget"]}
        if "temperature" in settings:
            body["temperature"] = settings["temperature"]
        if (
            len(json.dumps(body, ensure_ascii=False).encode()) + 4096
            > self.limits.max_input_tokens_per_call
        ):
            raise ProviderFailure("REQUIRED_CONTEXT_EXCEEDS_LIMIT")
        return body

    def send(self, body):
        key = os.environ.get(self.key_name)
        if not key:
            raise ProviderFailure("MISSING_PROVIDER_CREDENTIAL")
        if self.model.provider == "openai":
            url = "https://api.openai.com/v1/responses"
            headers = {"Authorization": "Bearer " + key}
        else:
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        try:
            response = self.client.post(url, headers=headers, json=body)
        except httpx.TransportError:
            # Usage is unknown. Caller retains the full reservation before any retry.
            raise ProviderFailure("PROVIDER_TRANSPORT_UNKNOWN", True) from None
        if response.status_code >= 400:
            # Never log upstream message text: it can echo prompts or credentials.
            provider_code = None
            try:
                error = response.json().get("error", {})
                candidate = error.get("code") or error.get("type")
                if candidate in (
                    "insufficient_quota",
                    "credit_balance_exhausted",
                    "rate_limit_exceeded",
                    "model_not_found",
                    "invalid_api_key",
                    "invalid_request_error",
                    "unsupported_parameter",
                    "unsupported_value",
                    "billing_hard_limit_reached",
                    "account_deactivated",
                ):
                    provider_code = candidate
            except (ValueError, AttributeError, TypeError):
                pass
            retryable = response.status_code in (429, 500, 502, 503, 504)
            if provider_code in (
                "insufficient_quota",
                "credit_balance_exhausted",
                "billing_hard_limit_reached",
            ):
                retryable = False
            raise ProviderFailure(
                "PROVIDER_HTTP_" + str(response.status_code),
                retryable,
                provider_code,
            )
        try:
            result = response.json()
        except ValueError:
            raise ProviderFailure("PROVIDER_RESPONSE_INVALID") from None
        self._remember_completed_response(result)
        return result

    def parse(self, response):
        if not isinstance(response, dict):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        if self.model.provider == "openai":
            output = response.get("output")
            if not isinstance(output, list) or any(not isinstance(v, dict) for v in output):
                raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
            calls = [v for v in output if v.get("type") == "function_call"]
            if (
                len(calls) != 1
                or calls[0].get("name") not in self.available_tools
                or response.get("status", "completed") != "completed"
                or calls[0].get("status", "completed") != "completed"
            ):
                raise ProtocolFailure("EXPECTED_ONE_COMPLETE_OPERATION")
            try:
                args = json.loads(calls[0]["arguments"])
            except (ValueError, KeyError, TypeError):
                raise ProtocolFailure("INVALID_OPERATION_JSON") from None
            return self._decode(calls[0]["name"], args)
        if not isinstance(response.get("content"), list) or any(
            not isinstance(v, dict) for v in response["content"]
        ):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        calls = [v for v in response.get("content", []) if v.get("type") == "tool_use"]
        if (
            len(calls) != 1
            or calls[0].get("name") not in self.available_tools
            or response.get("stop_reason") == "max_tokens"
        ):
            raise ProtocolFailure("EXPECTED_ONE_COMPLETE_OPERATION")
        return self._decode(calls[0]["name"], calls[0].get("input"))

    def _decode(self, name, arguments):
        self.last_tool_call = {"name": name, "arguments": arguments}
        if self.interface == "operate_v1":
            return arguments
        try:
            return decode_tool(name, arguments, interface=self.interface)
        except ValueError as error:
            raise ProtocolFailure(str(error)) from None

    def usage_cost(self, response, reserved):
        if not isinstance(response, dict):
            return reserved
        usage = response.get("usage") or {}
        if not isinstance(usage, dict):
            return reserved
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if (
            type(input_tokens) is not int
            or input_tokens < 0
            or type(output_tokens) is not int
            or output_tokens < 0
        ):
            return reserved
        if self.model.provider == "openai" and self.model.cached_input_usd_per_million is not None:
            details = usage.get("input_tokens_details")
            if not isinstance(details, dict):
                return reserved
            cached = details.get("cached_tokens")
            writes = details.get("cache_write_tokens")
            if (
                type(cached) is not int
                or type(writes) is not int
                or cached < 0
                or writes < 0
                or cached + writes > input_tokens
            ):
                return reserved
            return (
                (input_tokens - cached - writes) * self.model.input_usd_per_million
                + cached * self.model.cached_input_usd_per_million
                + writes * self.model.cache_write_input_usd_per_million
                + output_tokens * self.model.output_usd_per_million
            ) / 1_000_000
        # Cache creation can be priced higher on Anthropic. Until explicit cache pricing
        # is configured, retain the full reservation when cache writes are reported.
        if usage.get("cache_creation_input_tokens"):
            return reserved
        cached = usage.get("cache_read_input_tokens", 0)
        if type(cached) is not int or cached < 0:
            return reserved
        return (
            (input_tokens + cached) * self.model.input_usd_per_million
            + output_tokens * self.model.output_usd_per_million
        ) / 1_000_000
