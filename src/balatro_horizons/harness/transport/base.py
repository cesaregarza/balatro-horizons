"""One provider transport state machine driven by immutable field-name specs."""

import json
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from balatro_horizons.config import PROVIDER_TIMEOUT_SECONDS
from balatro_horizons.harness.contract import Context, Exchanges, RawOperation
from balatro_horizons.harness.input_limits import InputCounter, check_request_bytes
from balatro_horizons.harness.tool_interface import decode_tool


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


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    terminal_field: str
    terminal_map: dict[Any, str]
    call_item_type: str
    id_field: str
    args_field: str
    result_item_type: str
    cache_options: dict[str, Any] | None
    items_field: str
    default_terminal: Any
    success_terminals: frozenset[Any]
    terminal_detail: str
    clear_terminals: frozenset[Any]
    endpoint: str
    key_name: str
    headers: Callable[[str], dict[str, str]]
    payload: Callable[[Context, Exchanges], dict[str, Any]]
    request_body: Callable[["Transport", Context, Exchanges], dict[str, Any]]
    usage_cost: Callable[[Any, dict[str, Any], float], float]
    decode_arguments: Callable[[Any], Any]
    validate_call: Callable[[dict[str, Any]], None]
    terminal_details: Callable[[dict[str, Any], Any], dict[str, str]]
    native_messages: Callable[[list[dict[str, Any]], Any, "ProviderSpec"], list[dict[str, Any]]]
    synthetic_messages: Callable[[Any, Any, int, "ProviderSpec"], list[dict[str, Any]]]
    response_id_field: str | None = None
    completed_terminal: Any = None
    defer_unknown_terminal: bool = False


def encode(value, _ctx=None):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_messages(ctx):
    content = {"observation": ctx.observation, "omitted_event_ids": ctx.omitted_event_ids}
    if "current_costs" in ctx:
        # Dynamic prices follow the observation, outside the stable developer/tool prefix.
        content["current_costs"] = ctx.current_costs
    content.update(
        run_notebook=ctx.run_notebook,
        permitted_tools=ctx.allowed_tools,
        helper_status=ctx.helper_status,
        working_memory=ctx.working_memory,
        notebook_maintenance=ctx.notebook_maintenance,
    )
    if "previous_action_outcome" in ctx:
        content["previous_action_outcome"] = ctx.previous_action_outcome
    return [{"role": "user", "content": encode(content)}]


def tool_messages(ctx, exchanges, spec):
    messages = canonical_messages(ctx)
    for index, exchange in enumerate(exchanges):
        items = _native_items(exchange, spec)
        if items is not None:
            rendered = spec.native_messages(items, exchange["result"], spec)
        else:
            rendered = spec.synthetic_messages(
                exchange.get("tool_call"), exchange["result"], index, spec
            )
        messages.extend(rendered)
    return messages


def _native_items(exchange, spec):
    turn = exchange.get("provider_turn")
    if not isinstance(turn, dict) or turn.get("provider") != spec.name:
        return None
    items = turn.get("items")
    return deepcopy(items) if isinstance(items, list) else None


def context_payload(ctx, exchanges, provider):
    from balatro_horizons.harness.transport import provider_spec

    return provider_spec(provider).payload(ctx, exchanges)


class Transport:
    interface = "tools_v7"
    name = "model"
    actor = "agent"

    def __init__(self, model, limits, client=None):
        from balatro_horizons.harness.transport import provider_spec

        self.model, self.limits = model.model_copy(deep=True), limits.model_copy(deep=True)
        self.spec = provider_spec(model.provider)
        self.key_name = self.spec.key_name
        self.client = client or httpx.Client(timeout=PROVIDER_TIMEOUT_SECONDS, trust_env=False)
        self.last_request = None
        self.last_response = None
        self.available_tools = set()
        self.last_tool_call = None
        self.last_provider_turn = None
        self.input_counter = InputCounter()
        # Diagnostic comparison state is deliberately instance-local. RunService creates
        # a fresh provider for each run, so response IDs cannot cross episode lifecycles.
        self._last_completed_openai_response_id = None

    def request(self, ctx, exchanges):
        self.available_tools = set(ctx.allowed_tools)
        self.last_tool_call = None
        body = self.spec.request_body(self, ctx, exchanges)
        check_request_bytes(body, self.limits)
        return body

    def tool_messages(self, ctx, exchanges):
        return tool_messages(ctx, exchanges, self.spec)

    def decide(self, context: Context, exchanges: Exchanges) -> RawOperation:
        raise RuntimeError("PROVIDER_DECISION_REQUIRES_METERED_TRANSPORT")

    def on_decision_end(self) -> None:
        # Continuation artifacts may cross helper turns, never game actions.
        self.last_tool_call = None
        self.last_provider_turn = None

    def on_commit(self) -> None:
        pass

    def check_input(self, body):
        return self.input_counter.check(self, body)

    def send(self, body):
        key = os.environ.get(self.spec.key_name)
        if not key:
            raise ProviderFailure("MISSING_PROVIDER_CREDENTIAL")
        self.check_input(body)
        try:
            response = self.client.post(
                self.spec.endpoint, headers=self.spec.headers(key), json=body
            )
        except httpx.TransportError:
            raise ProviderFailure("PROVIDER_TRANSPORT_UNKNOWN", True) from None
        self._check_status(response)
        try:
            result = response.json()
        except ValueError:
            raise ProviderFailure("PROVIDER_RESPONSE_INVALID") from None
        self._remember_completed_response(result)
        return result

    def _check_status(self, response):
        if response.status_code < 400:
            return
        provider_code = _safe_provider_code(response)
        retryable = response.status_code in (429, 500, 502, 503, 504)
        if provider_code in (
            "insufficient_quota",
            "credit_balance_exhausted",
            "billing_hard_limit_reached",
        ):
            retryable = False
        raise ProviderFailure(
            "PROVIDER_HTTP_" + str(response.status_code), retryable, provider_code
        )

    def parse(self, response):
        items = self._response_items(response)
        self._capture_turn(items, self.spec.call_item_type, self.spec.id_field)
        terminal = self._terminal_gate(response)
        calls = self._extract_calls(items)
        call = self._single_call(calls, terminal)
        self._call_identity(call)
        self._available_call(call)
        arguments = self._decode_arguments(call)
        return self._decode(call["name"], arguments)

    def _response_items(self, response):
        if not isinstance(response, dict):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        items = response.get(self.spec.items_field)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        return items

    def _terminal_gate(self, response):
        terminal = response.get(self.spec.terminal_field, self.spec.default_terminal)
        if code := self.spec.terminal_map.get(terminal):
            if terminal in self.spec.clear_terminals:
                self.last_provider_turn = None
            details = {self.spec.terminal_detail: terminal}
            details.update(self.spec.terminal_details(response, terminal))
            raise ProtocolFailure(code, **details)
        if terminal not in self.spec.success_terminals and not self.spec.defer_unknown_terminal:
            self.last_provider_turn = None
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        return terminal

    def _extract_calls(self, items):
        return [item for item in items if item.get("type") == self.spec.call_item_type]

    def _single_call(self, calls, terminal):
        if not calls:
            details = (
                {self.spec.terminal_detail: terminal}
                if terminal not in self.spec.success_terminals and isinstance(terminal, str)
                else {}
            )
            raise ProtocolFailure("NO_OPERATION", **details)
        if terminal not in self.spec.success_terminals:
            raise ProtocolFailure(
                "UNEXPECTED_PROVIDER_STOP", **{self.spec.terminal_detail: str(terminal)}
            )
        if len(calls) != 1:
            raise ProtocolFailure("MULTIPLE_OPERATIONS", operation_count=len(calls))
        return calls[0]

    def _available_call(self, call):
        if call.get("name") not in self.available_tools:
            raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(call.get("name")))

    def _call_identity(self, call):
        try:
            self.spec.validate_call(call)
        except ProtocolFailure:
            self.last_provider_turn = None
            raise
        identifier = call.get(self.spec.id_field)
        if not isinstance(identifier, str) or not identifier:
            self.last_provider_turn = None
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")

    def _decode_arguments(self, call):
        try:
            return self.spec.decode_arguments(call.get(self.spec.args_field))
        except (ValueError, KeyError, TypeError):
            raise ProtocolFailure("INVALID_OPERATION_JSON") from None

    def _capture_turn(self, items, call_type, id_field):
        """Retain opaque provider blocks exactly for this decision only."""
        calls = [item for item in items if item.get("type") == call_type]
        if any(not isinstance(call.get(id_field), str) or not call[id_field] for call in calls):
            return
        self.last_provider_turn = {
            "version": "provider_turn_v1",
            "provider": self.spec.name,
            "items": deepcopy(items),
        }

    def _remember_completed_response(self, response):
        if self.spec.response_id_field is None or not isinstance(response, dict):
            return
        response_id = response.get(self.spec.response_id_field)
        # Missing status is not evidence of completion. Keep the last known completed
        # baseline across incomplete, failed, malformed, and transport-failed responses.
        if (
            response.get(self.spec.terminal_field) == self.spec.completed_terminal
            and isinstance(response_id, str)
            and response_id
        ):
            self._last_completed_openai_response_id = response_id

    def _decode(self, name, arguments):
        self.last_tool_call = {"name": name, "arguments": arguments}
        try:
            return decode_tool(name, arguments)
        except ValueError as error:
            raise ProtocolFailure(str(error)) from None

    def usage_cost(self, response, reserved):
        return self.spec.usage_cost(self.model, response, reserved)


def _safe_provider_code(response):
    # Never log upstream message text: it can echo prompts or credentials.
    allowed = {
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
    }
    try:
        error = response.json().get("error", {})
        candidate = error.get("code") or error.get("type")
        return candidate if candidate in allowed else None
    except (ValueError, AttributeError, TypeError):
        return None
