"""Direct transport adapters; canonical context and tool behavior live elsewhere."""

import json
import os
import re
from copy import deepcopy

import httpx

from balatro_horizons.agents.input_limits import InputCounter, check_request_bytes
from balatro_horizons.agents.protocol import TOOL
from balatro_horizons.agents.tool_interface import (
    CONTINUATION_INTERFACES,
    FOCUSED_INTERFACES,
    NAMED_INTERFACES,
    NOTEBOOK_INTERFACES,
    STABLE_TOOL_INTERFACES,
    WORKING_MEMORY_INTERFACE,
    decode_tool,
)
from balatro_horizons.config import PROVIDER_TIMEOUT_SECONDS


class ProviderFailure(RuntimeError):
    def __init__(self, code, retryable=False, provider_code=None):
        self.code, self.retryable = code, retryable
        self.provider_code = provider_code
        super().__init__(code)


class ProtocolFailure(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


def encode(value, ctx):
    separators = (",", ":") if ctx.get("interface_version") in FOCUSED_INTERFACES else None
    return json.dumps(value, ensure_ascii=False, separators=separators)


def canonical_messages(ctx, exchanges):
    content = {"observation": ctx["observation"], "omitted_event_ids": ctx["omitted_event_ids"]}
    if "current_costs" in ctx:
        # Dynamic prices follow the observation, outside the stable developer/tool prefix.
        content["current_costs"] = ctx["current_costs"]
    if ctx.get("interface_version") in NOTEBOOK_INTERFACES:
        content.update(run_notebook=ctx["run_notebook"], permitted_tools=ctx["allowed_tools"],
                       helper_status=ctx["helper_status"])
    if ctx.get("interface_version") == WORKING_MEMORY_INTERFACE:
        content.update(working_memory=ctx["working_memory"],
                       notebook_maintenance=ctx["notebook_maintenance"])
    messages = [
        {
            "role": "user",
            "content": encode(content, ctx),
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
    """Serialize tool exchanges, preserving native turns when the protocol requires it."""
    messages = canonical_messages(ctx, [])
    for index, exchange in enumerate(exchanges):
        turn = exchange.get("provider_turn")
        if isinstance(turn, dict) and turn.get("provider") == provider:
            items = turn.get("items")
            if isinstance(items, list):
                output = encode(exchange["result"], ctx)
                is_error = isinstance(exchange["result"], dict) and bool(
                    exchange["result"].get("error")
                )
                if provider == "openai":
                    messages.extend(deepcopy(items))
                    calls = [item for item in items if item.get("type") == "function_call"]
                    if calls:
                        messages.extend(
                            {
                                "type": "function_call_output",
                                "call_id": call["call_id"],
                                "output": output,
                            }
                            for call in calls
                        )
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": encode({"tool_error": exchange["result"]}, ctx),
                            }
                        )
                else:
                    messages.append({"role": "assistant", "content": deepcopy(items)})
                    calls = [item for item in items if item.get("type") == "tool_use"]
                    if calls:
                        results = []
                        for call in calls:
                            result = {
                                "type": "tool_result",
                                "tool_use_id": call["id"],
                                "content": output,
                            }
                            if is_error:
                                result["is_error"] = True
                            results.append(result)
                        messages.append({"role": "user", "content": results})
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": encode({"tool_error": exchange["result"]}, ctx),
                                    }
                                ],
                            }
                        )
                continue
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
    definitions = ctx["tools"] if interface in NAMED_INTERFACES else [ctx.get("tool", TOOL)]
    if provider == "openai":
        if interface in STABLE_TOOL_INTERFACES:
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
                "tool_choice": (
                    {
                        "type": "allowed_tools",
                        "mode": "auto",
                        "tools": [
                            {"type": "function", "name": name} for name in ctx["allowed_tools"]
                        ],
                    }
                    if interface == "tools_v4"
                    else "auto"
                ),
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
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
                **({"strict": True} if interface in CONTINUATION_INTERFACES else {}),
            }
            for t in definitions
        ],
    }


class DirectProvider:
    paid = True

    def __init__(self, model, limits, client=None):
        self.model, self.limits = model.model_copy(deep=True), limits.model_copy(deep=True)
        self.client = client or httpx.Client(timeout=PROVIDER_TIMEOUT_SECONDS, trust_env=False)
        self.key_name = "OPENAI_API_KEY" if model.provider == "openai" else "ANTHROPIC_API_KEY"
        self.last_request = None
        self.last_response = None
        self.interface = model.settings.get("harness_interface", "operate_v1")
        self.available_tools = {"operate"}
        self.last_tool_call = None
        self.last_provider_turn = None
        self.input_counter = InputCounter()
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
        definitions = (
            ctx["tools"] if self.interface in NAMED_INTERFACES else [ctx.get("tool", TOOL)]
        )
        payload = context_payload(ctx, exchanges, self.model.provider, self.interface)
        self.available_tools = (
            set(ctx["allowed_tools"])
            if self.interface in STABLE_TOOL_INTERFACES
            else {tool["name"] for tool in definitions}
        )
        self.last_tool_call = None
        self.last_provider_turn = None
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
            if self.interface in STABLE_TOOL_INTERFACES:
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
            if self.interface in CONTINUATION_INTERFACES:
                # store=false is deliberate. Encrypted reasoning makes returned
                # reasoning items round-trippable during this one decision.
                body["include"] = ["reasoning.encrypted_content"]
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
        check_request_bytes(body, self.limits)
        return body

    def check_input(self, body):
        return self.input_counter.check(self, body)

    def send(self, body):
        key = os.environ.get(self.key_name)
        if not key:
            raise ProviderFailure("MISSING_PROVIDER_CREDENTIAL")
        # Also protects CLI probes that use send directly. Exact retries reuse the count.
        self.check_input(body)
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
            if self.interface not in CONTINUATION_INTERFACES:
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
            self._capture_turn(output, "function_call", "call_id")
            status = response.get("status", "completed")
            if status == "incomplete":
                self.last_provider_turn = None
                details = response.get("incomplete_details")
                reason = details.get("reason") if isinstance(details, dict) else None
                raise ProtocolFailure(
                    "PROVIDER_RESPONSE_INCOMPLETE",
                    provider_status="incomplete",
                    **({"provider_reason": reason} if isinstance(reason, str) else {}),
                )
            if status in ("failed", "cancelled"):
                self.last_provider_turn = None
                raise ProtocolFailure("PROVIDER_RESPONSE_FAILED", provider_status=status)
            if status in ("in_progress", "queued"):
                self.last_provider_turn = None
                raise ProtocolFailure("PROVIDER_RESPONSE_UNRESOLVED", provider_status=status)
            if status != "completed":
                self.last_provider_turn = None
                raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
            calls = [v for v in output if v.get("type") == "function_call"]
            if not calls:
                raise ProtocolFailure("NO_OPERATION")
            if len(calls) != 1:
                raise ProtocolFailure("MULTIPLE_OPERATIONS", operation_count=len(calls))
            call = calls[0]
            call_status = call.get("status", "completed")
            if call_status == "incomplete":
                self.last_provider_turn = None
                raise ProtocolFailure("PROVIDER_RESPONSE_INCOMPLETE", provider_status="incomplete")
            if call_status != "completed":
                self.last_provider_turn = None
                raise ProtocolFailure(
                    "PROVIDER_RESPONSE_UNRESOLVED", provider_status=str(call_status)
                )
            if self.interface in CONTINUATION_INTERFACES and (
                not isinstance(call.get("call_id"), str) or not call["call_id"]
            ):
                self.last_provider_turn = None
                raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
            if call.get("name") not in self.available_tools:
                raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(call.get("name")))
            try:
                args = json.loads(call["arguments"])
            except (ValueError, KeyError, TypeError):
                raise ProtocolFailure("INVALID_OPERATION_JSON") from None
            return self._decode(call["name"], args)
        if not isinstance(response.get("content"), list) or any(
            not isinstance(v, dict) for v in response["content"]
        ):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        content = response["content"]
        if self.interface not in CONTINUATION_INTERFACES:
            calls = [v for v in content if v.get("type") == "tool_use"]
            if (
                len(calls) != 1
                or calls[0].get("name") not in self.available_tools
                or response.get("stop_reason") == "max_tokens"
            ):
                raise ProtocolFailure("EXPECTED_ONE_COMPLETE_OPERATION")
            return self._decode(calls[0]["name"], calls[0].get("input"))
        self._capture_turn(content, "tool_use", "id")
        stop_reason = response.get("stop_reason")
        if stop_reason == "max_tokens":
            self.last_provider_turn = None
            raise ProtocolFailure("PROVIDER_RESPONSE_INCOMPLETE", provider_stop_reason=stop_reason)
        if stop_reason == "model_context_window_exceeded":
            self.last_provider_turn = None
            raise ProtocolFailure("PROVIDER_CONTEXT_LIMIT", provider_stop_reason=stop_reason)
        if stop_reason == "refusal":
            raise ProtocolFailure("PROVIDER_REFUSAL", provider_stop_reason=stop_reason)
        if stop_reason == "pause_turn":
            self.last_provider_turn = None
            raise ProtocolFailure("PROVIDER_RESPONSE_PAUSED", provider_stop_reason=stop_reason)
        calls = [v for v in content if v.get("type") == "tool_use"]
        if not calls:
            raise ProtocolFailure(
                "NO_OPERATION",
                **({"provider_stop_reason": stop_reason} if isinstance(stop_reason, str) else {}),
            )
        if stop_reason not in (None, "tool_use"):
            raise ProtocolFailure(
                "UNEXPECTED_PROVIDER_STOP",
                provider_stop_reason=str(stop_reason),
            )
        if len(calls) != 1:
            raise ProtocolFailure("MULTIPLE_OPERATIONS", operation_count=len(calls))
        call = calls[0]
        if self.interface in CONTINUATION_INTERFACES and (
            not isinstance(call.get("id"), str) or not call["id"]
        ):
            self.last_provider_turn = None
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        if call.get("name") not in self.available_tools:
            raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(call.get("name")))
        return self._decode(call["name"], call.get("input"))

    def _capture_turn(self, items, call_type, id_field):
        """Retain opaque provider blocks exactly for this decision only."""
        if self.interface not in CONTINUATION_INTERFACES:
            return
        calls = [item for item in items if item.get("type") == call_type]
        if any(not isinstance(call.get(id_field), str) or not call[id_field] for call in calls):
            return
        self.last_provider_turn = {
            "version": "provider_turn_v1",
            "provider": self.model.provider,
            "items": deepcopy(items),
        }

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
