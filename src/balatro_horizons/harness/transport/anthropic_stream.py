"""Assemble complete Messages while retaining signed/opaque content verbatim.

Wire contract: https://platform.claude.com/docs/en/build-with-claude/streaming
"""

from copy import deepcopy

from balatro_horizons.harness.transport.errors import ProviderFailure, native_code
from balatro_horizons.harness.transport.sse import strict_json

TOOL_INPUT_ERROR = "_harness_tool_input_error"


class MessageStream:
    def __init__(self):
        self.message = None
        self.open_blocks = set()
        self.json_fragments = {}
        self.final_usage = False
        self.stopped = False

    def assemble(self, stream):
        for event in stream:
            self.accept(event)
            if self.stopped:
                return self.message
        raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")

    def accept(self, event):
        kind = event["type"]
        if kind == "error":
            raise ProviderFailure("PROVIDER_STREAM_ERROR", provider_code=native_code(event, "anthropic"))
        if kind == "ping":
            return
        if kind == "message_start":
            self._start(event.get("message"))
            return
        if self.message is None:
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        if kind == "content_block_start":
            self._block_start(event)
        elif kind == "content_block_delta":
            self._block_delta(event)
        elif kind == "content_block_stop":
            self._block_stop(event)
        elif kind == "message_delta":
            self._message_delta(event)
        elif kind == "message_stop":
            if self.open_blocks or not isinstance(self.message.get("stop_reason"), str):
                raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")
            if not self.final_usage:
                # The message_start output count is partial, never a settlement total.
                self.message["usage"].pop("output_tokens", None)
            self.stopped = True

    def _start(self, message):
        if (self.message is not None or not isinstance(message, dict)
                or not isinstance(message.get("id"), str) or not message["id"]
                or message.get("content") != [] or message.get("role") != "assistant"
                or message.get("type") != "message"):
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        self.message = deepcopy(message)
        self.message.pop(TOOL_INPUT_ERROR, None)
        if not isinstance(self.message.get("usage"), dict):
            self.message["usage"] = {}

    def _block_start(self, event):
        index, block = event.get("index"), event.get("content_block")
        if (type(index) is not int or index != len(self.message["content"])
                or not isinstance(block, dict) or not isinstance(block.get("type"), str)):
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        self.message["content"].append(deepcopy(block))
        self.open_blocks.add(index)

    def _block(self, event):
        index = event.get("index")
        if type(index) is not int or index not in self.open_blocks:
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        return index, self.message["content"][index]

    def _block_delta(self, event):
        index, block = self._block(event)
        delta = event.get("delta")
        if not isinstance(delta, dict):
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        kind = delta.get("type")
        if kind == "input_json_delta":
            value = delta.get("partial_json")
            if block["type"] != "tool_use" or not isinstance(value, str):
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            self.json_fragments.setdefault(index, []).append(value)
        elif kind == "citations_delta":
            if block["type"] != "text" or not isinstance(delta.get("citation"), dict):
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            block.setdefault("citations", []).append(deepcopy(delta["citation"]))
        elif kind in {"text_delta", "thinking_delta", "signature_delta"}:
            field = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}[kind]
            expected = "text" if kind == "text_delta" else "thinking"
            if (block["type"] != expected or not isinstance(delta.get(field), str)
                    or not isinstance(block.get(field, ""), str)):
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            block[field] = block.get(field, "") + delta[field]
        else:
            # Unknown top-level events are ignorable; unknown mutations are not replayable.
            raise ProviderFailure("PROVIDER_STREAM_UNSUPPORTED_DELTA")

    def _block_stop(self, event):
        index, block = self._block(event)
        if index in self.json_fragments:
            raw = "".join(self.json_fragments.pop(index))
            block["input"], error = _tool_input(raw)
            if error:
                self.message[TOOL_INPUT_ERROR] = error
        if block["type"] == "thinking" and (
            not isinstance(block.get("signature"), str) or not block["signature"]
        ):
            raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")
        self.open_blocks.remove(index)

    def _message_delta(self, event):
        delta, usage = event.get("delta"), event.get("usage")
        if (self.open_blocks or not isinstance(delta, dict)
                or set(delta) & {"content", "usage", "id", "type", "role", TOOL_INPUT_ERROR}):
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        self.message.update(deepcopy(delta))
        if isinstance(usage, dict):
            self.message["usage"].update(deepcopy(usage))
            self.final_usage = "output_tokens" in usage


def assemble(stream):
    return MessageStream().assemble(stream)


def _tool_input(raw):
    try:
        value = strict_json(raw)
    except (ValueError, RecursionError):
        error = "INVALID_OPERATION_JSON"
    else:
        if isinstance(value, dict):
            return value, None
        error = "TOOL_ARGUMENTS_MUST_BE_OBJECT"
    # Claude requires an object in replayed tool_use blocks. Explicitly wrap
    # the original bad input, never substitute executable arguments. Parsing
    # reports the error only after complete terminal/usage evidence is metered.
    return {"INVALID_JSON": raw}, error
