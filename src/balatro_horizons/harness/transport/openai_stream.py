"""Responses terminal assembly; output deltas never become executable calls.

Wire contract: https://developers.openai.com/api/reference/resources/responses/streaming-events
"""

from copy import deepcopy

from balatro_horizons.harness.transport.errors import ProviderFailure, native_code


def assemble(stream):
    completed_items, response_id = {}, None
    for event in stream:
        kind = event["type"]
        if kind == "error":
            raise ProviderFailure("PROVIDER_STREAM_ERROR", provider_code=native_code(event, "openai"))
        if kind == "response.created":
            initial = event.get("response")
            if (response_id is not None or not isinstance(initial, dict)
                    or not isinstance(initial.get("id"), str) or not initial["id"]):
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            response_id = initial["id"]
        elif kind == "response.output_item.done":
            index, item = event.get("output_index"), event.get("item")
            if type(index) is not int or index < 0 or not isinstance(item, dict):
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            if index in completed_items:
                raise ProviderFailure("PROVIDER_STREAM_INVALID")
            completed_items[index] = deepcopy(item)
        elif kind in {"response.completed", "response.failed", "response.incomplete"}:
            return _terminal(event, response_id, completed_items)
    raise ProviderFailure("PROVIDER_STREAM_INCOMPLETE")


def _terminal(event, response_id, completed_items):
    response = event.get("response")
    if (not isinstance(response, dict) or response.get("status") != event["type"].split(".")[1]
            or not isinstance(response.get("id"), str) or not response["id"]
            or response_id is not None and response["id"] != response_id):
        raise ProviderFailure("PROVIDER_STREAM_INVALID")
    if response["status"] == "completed" and response.get("error") is not None:
        raise ProviderFailure("PROVIDER_STREAM_INVALID")
    output = response.get("output")
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        raise ProviderFailure("PROVIDER_STREAM_INVALID")
    result = deepcopy(response)
    for index, item in completed_items.items():
        if index >= len(output) or any(key in output[index] and output[index][key] != value
                                     for key, value in item.items()):
            raise ProviderFailure("PROVIDER_STREAM_INVALID")
        # output_item.done carries final encrypted content, never the added/delta item.
        result["output"][index] = {**output[index], **item}
    return result
