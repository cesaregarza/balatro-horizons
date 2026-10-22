"""Bounded SSE framing, preserving bytes across arbitrary UTF-8 chunk boundaries."""

import codecs
import json
import re

from balatro_horizons.harness.transport.errors import ProviderFailure

MAX_STREAM_BYTES = 32 * 1024 * 1024


def strict_json(value):
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")

    return json.loads(value, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def _lines(response, check_stop):
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, size = "", 0
    try:
        for chunk in response.iter_bytes():
            check_stop()
            size += len(chunk)
            if size > MAX_STREAM_BYTES:
                raise ProviderFailure("PROVIDER_STREAM_LIMIT")
            buffer += decoder.decode(chunk)
            lines, buffer = _split_lines(buffer)
            yield from lines
        buffer += decoder.decode(b"", final=True)
    except UnicodeError:
        raise ProviderFailure("PROVIDER_STREAM_INVALID") from None
    check_stop()
    lines, buffer = _split_lines(buffer, final=True)
    yield from lines
    if buffer:
        raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")


def _split_lines(buffer, *, final=False):
    lines, start = [], 0
    for match in re.finditer(r"\r\n|\r|\n", buffer):
        if not final and match.group() == "\r" and match.end() == len(buffer):
            break
        lines.append(buffer[start:match.start()])
        start = match.end()
    return lines, buffer[start:]


def events(response, check_stop):
    data, event_name = [], None
    for line in _lines(response, check_stop):
        if not line:
            if data:
                event = _event("\n".join(data), event_name)
                if event is not None:
                    yield event
            data, event_name = [], None
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line.startswith("event:"):
            event_name = line[6:].removeprefix(" ")
    if data:
        raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")


def _event(data, name):
    if data == "[DONE]":
        return None
    try:
        event = strict_json(data)
    except (ValueError, RecursionError):
        raise ProviderFailure("PROVIDER_STREAM_INVALID") from None
    if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
            or name is not None and name != event["type"]):
        raise ProviderFailure("PROVIDER_STREAM_INVALID")
    return event
