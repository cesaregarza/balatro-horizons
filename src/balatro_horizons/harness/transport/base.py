"""Shared HTTP mechanics and canonical operation decoding, without provider policy."""

import os
from abc import ABC, abstractmethod
from copy import deepcopy

import httpx

from balatro_horizons.config import PROVIDER_TIMEOUT_SECONDS
from balatro_horizons.harness.contract import Context, Exchanges, RawOperation
from balatro_horizons.harness.input_limits import InputCounter, check_request_bytes
from balatro_horizons.harness.tool_interface import decode_tool
from balatro_horizons.harness.transport.errors import ProtocolFailure, ProviderFailure, check_status
from balatro_horizons.harness.transport.schema import validate_arguments
from balatro_horizons.harness.transport.sse import events


class Transport(ABC):
    """Common implementation of the runner's metered ProviderPolicy boundary."""

    interface = "tools_v8"
    name = "model"
    actor = "agent"

    def __init__(self, model, limits, client=None):
        self.model, self.limits = model.model_copy(deep=True), limits.model_copy(deep=True)
        # Streaming bounds each stalled read, rather than waiting for a full generation.
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS), trust_env=False,
        )
        self.last_request = self.last_response = None
        self.available_tools, self.tool_schemas = set(), {}
        self.last_tool_call = self.last_provider_turn = self.model_references = None
        self.input_counter = InputCounter()
        self._stopped = lambda: False

    def bind_stop(self, stopped):
        self._stopped = stopped

    def _check_stop(self):
        if self._stopped():
            raise ProviderFailure("PROVIDER_CANCELLED")

    def request(self, ctx, exchanges):
        from balatro_horizons.harness.transport import validate_runtime

        validate_runtime(self.model, self.limits, ctx.tools)
        self.available_tools = set(ctx.allowed_tools)
        self.tool_schemas = {tool["name"]: deepcopy(tool["parameters"]) for tool in ctx.tools}
        self.model_references = ctx.model_references
        self.last_tool_call = None
        body = self.request_body(ctx, exchanges)
        check_request_bytes(body, self.limits)
        return body

    @abstractmethod
    def request_body(self, ctx, exchanges): ...

    @abstractmethod
    def parse(self, response): ...

    @abstractmethod
    def assemble(self, stream): ...

    @abstractmethod
    def usage_cost(self, response, reserved): ...

    @abstractmethod
    def usage_known(self, response): ...

    def decide(self, context: Context, exchanges: Exchanges) -> RawOperation:
        raise RuntimeError("PROVIDER_DECISION_REQUIRES_METERED_TRANSPORT")

    def on_decision_end(self) -> None:
        self.last_tool_call = self.last_provider_turn = None
        self.model_references = None

    def on_commit(self) -> None:
        return None

    def check_input(self, body):
        self._check_stop()
        return self.input_counter.check(self, body)

    def send(self, body):
        key = os.environ.get(self.key_name)
        if not key:
            raise ProviderFailure("MISSING_PROVIDER_CREDENTIAL")
        self.check_input(body)
        self._check_stop()
        admitted = False
        try:
            with self.client.stream("POST", self.endpoint, headers=self.headers(key), json=body) as response:
                if response.status_code >= 400:
                    response.read()
                check_status(response, self.model.provider)
                admitted = True
                if response.headers.get("content-type", "").split(";")[0].strip().lower() != "text/event-stream":
                    raise ProviderFailure("PROVIDER_STREAM_REQUIRED")
                result = self.assemble(events(response, self._check_stop))
        except httpx.ReadTimeout:
            raise ProviderFailure("PROVIDER_READ_TIMEOUT") from None
        except (httpx.ReadError, httpx.RemoteProtocolError):
            raise ProviderFailure("PROVIDER_RESPONSE_LOST") from None
        except (httpx.WriteError, httpx.WriteTimeout):
            # A failed write may have delivered the request before losing acknowledgement.
            raise ProviderFailure("PROVIDER_TRANSPORT_UNKNOWN") from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderFailure("PROVIDER_TRANSPORT_UNKNOWN", not admitted) from None
        except httpx.TransportError:
            raise ProviderFailure("PROVIDER_TRANSPORT_UNKNOWN") from None
        self._check_stop()
        self.remember_response(result)
        return result

    def remember_response(self, response):
        return None

    def capture_turn(self, items, call_type, id_field):
        self.last_provider_turn = None
        calls = [item for item in items if item.get("type") == call_type]
        identifiers = [call.get(id_field) for call in calls]
        if (any(not isinstance(value, str) or not value for value in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
        self.last_provider_turn = {
            "version": "provider_turn_v1", "provider": self.model.provider,
            "items": deepcopy(items),
        }
        return calls

    def decode_call(self, name, arguments):
        if not isinstance(name, str) or name not in self.available_tools:
            raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(name))
        self.last_tool_call = {"name": name, "arguments": deepcopy(arguments)}
        schema = self.tool_schemas.get(name)
        if schema is None:
            raise ProtocolFailure("UNAVAILABLE_TOOL", attempted_tool=str(name))
        validate_arguments(arguments, schema)
        try:
            if self.model_references is not None:
                arguments = self.model_references.arguments(arguments, fields=set(schema["properties"]))
            return decode_tool(name, arguments)
        except ValueError as error:
            raise ProtocolFailure(str(error)) from None


def response_items(response, field):
    if not isinstance(response, dict):
        raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
    items = response.get(field)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ProtocolFailure("INVALID_PROVIDER_RESPONSE")
    return items


def single_call(calls):
    if not calls:
        raise ProtocolFailure("NO_OPERATION")
    if len(calls) != 1:
        raise ProtocolFailure("MULTIPLE_OPERATIONS", operation_count=len(calls))
    return calls[0]
