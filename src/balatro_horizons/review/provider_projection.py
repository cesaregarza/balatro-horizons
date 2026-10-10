"""Provider display semantics; never spending settlement or journal mutation."""

import re


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def reported_usage(body):
    """Claude cache categories are disjoint; Responses input is inclusive."""
    body = body if isinstance(body, dict) else {}
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _tokens(usage.get("input_tokens"))
    anthropic = "output" not in body and (
        body.get("type") == "message" or "content" in body
        or "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage
    )
    if anthropic:
        read = _tokens(usage.get("cache_read_input_tokens", 0))
        write = _tokens(usage.get("cache_creation_input_tokens", 0))
        input_tokens = (input_tokens + read + write
                        if all(value is not None for value in (input_tokens, read, write)) else None)
    else:
        details = usage.get("input_tokens_details")
        details = details if isinstance(details, dict) else {}
        read = _tokens(details.get("cached_tokens", usage.get("cache_read_tokens")))
        write = _tokens(details.get("cache_write_tokens", usage.get("cache_write_tokens")))
    return {"input_tokens": input_tokens, "output_tokens": _tokens(usage.get("output_tokens")),
            "cache_read_tokens": read, "cache_write_tokens": write}


def _summarized_claude(model):
    # Claude 3.7 returned full thinking. Unknown model provenance is not enough
    # to relabel a historical thinking block as a public summary.
    return isinstance(model, str) and bool(re.fullmatch(
        r"claude-(?:(?:opus|sonnet|haiku)-(?:4|5)(?:[-.][a-z0-9]+)*|"
        r"(?:fable|mythos)-(?:5(?:[-.][a-z0-9]+)*|preview))", model
    ))


def returned_reasoning(body):
    """Allow only documented returned summary text, never opaque block fields."""
    body = body if isinstance(body, dict) else {}
    texts, seen, redacted, unsupported = [], False, False, False
    output = body.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        seen = True
        parts = item.get("summary")
        for part in parts if isinstance(parts, list) else []:
            if (isinstance(part, dict) and part.get("type") == "summary_text"
                    and isinstance(part.get("text"), str) and part["text"].strip()):
                texts.append(part["text"])
    content = body.get("content")
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "redacted_thinking":
            redacted = True
        elif block.get("type") == "thinking":
            seen = True
            if not _summarized_claude(body.get("model")):
                unsupported = True
            elif isinstance(block.get("thinking"), str) and block["thinking"].strip():
                texts.append(block["thinking"])
    status = ("returned" if texts else "unsupported" if unsupported else "redacted" if redacted
              else "empty" if seen else "absent")
    return {"texts": texts, "status": status, "redacted": redacted}


def response_projection(event):
    """Add display fields while keeping the native body for technical inspection."""
    if event["type"] != "provider_response":
        return event
    payload = event["payload"]
    if "reported_usage" in payload and "returned_reasoning" in payload:
        return event  # Already projected by the compact verified-read cache.
    body = payload.get("body")
    return {**event, "payload": {**payload, "reported_usage": reported_usage(body),
                                 "returned_reasoning": returned_reasoning(body)}}
