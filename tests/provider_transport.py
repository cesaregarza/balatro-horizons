"""Counting response for tests whose subject is the generation transport."""

import json
from copy import deepcopy

import httpx

from balatro_horizons.harness.baselines import Baseline


def model_choice(context):
    """Mock a model using only delivered integers, never runner/backend handles."""
    scalars = {"id", "blind_id", "offer_id", "owned_id", "consumable_id", "current_blind_id"}
    lists = {"card_ids", "ordered_ids", "target_ids", "required_ids", "current_hand_ids"}
    references = {}

    def convert(value, translate, key=None):
        if key in scalars:
            return translate(value)
        if key in lists and isinstance(value, list):
            return [translate(item) for item in value]
        if isinstance(value, dict):
            return {name: convert(item, translate, name) for name, item in value.items()}
        if isinstance(value, list):
            return [convert(item, translate) for item in value]
        return value

    def opaque(index):
        if type(index) is not int:
            return index
        identifier = f"mock-object-{index}"
        references[identifier] = index
        return identifier

    # The canonical validator used by Baseline intentionally still requires strings.
    presented = convert(context, opaque)
    for offer in presented["observation"]["state"]["offers"]:
        if "quote" in offer:
            # The fake model reads the delivered quote, not a canonical side channel.
            quote = offer.pop("quote")
            offer.setdefault("price", quote["cash_cost"])
    choice = Baseline("heuristic").decide(presented, [])
    return convert(choice, lambda value: references.get(value, value))


def with_input_count(receive):
    def wrapped(request):
        if request.url.path.endswith(("/input_tokens", "/count_tokens")):
            return httpx.Response(200, json={"input_tokens": 100})
        response = receive(request)
        if response.status_code >= 400 or response.headers.get("content-type") == "text/event-stream":
            return response
        body = json.loads(request.content)
        if body.get("stream"):
            provider = "anthropic" if request.url.path == "/v1/messages" else "openai"
            return stream_response(response.json(), provider)
        return response

    return wrapped


def stream_response(message, provider):
    """Represent a mock native response with the provider's actual SSE envelope."""
    message = deepcopy(message)
    message.setdefault("id", "mock-response")
    if provider == "openai":
        message.setdefault("status", "completed")
        message.setdefault("output", [])
        events = [{"type": "response." + message["status"], "response": message}]
    else:
        blocks = message.pop("content", [])
        reason = message.pop("stop_reason", "tool_use" if any(
            block.get("type") == "tool_use" for block in blocks) else "end_turn")
        usage = message.pop("usage", {})
        message.update(type="message", role="assistant", content=[], stop_reason=None,
                       usage={key: value for key, value in usage.items() if key != "output_tokens"})
        events = [{"type": "message_start", "message": message}]
        for index, block in enumerate(blocks):
            events.extend([
                {"type": "content_block_start", "index": index, "content_block": block},
                {"type": "content_block_stop", "index": index},
            ])
        events.extend([
            {"type": "message_delta", "delta": {"stop_reason": reason}, "usage": usage},
            {"type": "message_stop"},
        ])
    content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
