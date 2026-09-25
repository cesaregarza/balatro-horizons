"""Compact the model's delivery, without changing canonical context or evidence."""

import json
from copy import deepcopy

from balatro_horizons.config import EVENT_SUMMARY_CHARACTERS

LAST_ACTION_REF = "/observation/last_action"


def truncate_summaries(view):
    """Bound only default delivery, after complete summaries have integer IDs."""
    for event in view["observation"].get("recent_public_events", []):
        if len(event["summary"]) > EVENT_SUMMARY_CHARACTERS:
            event["summary"] = event["summary"][:EVENT_SUMMARY_CHARACTERS]
            event["truncated"] = True
    return view


def same_value(left, right):
    """JSON equality must distinguish false, zero, and numeric-looking strings."""
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False
    )


def _matched_quotes(observation, costs):
    offers = observation.get("state", {}).get("offers", [])
    quotes = costs.get("offers")
    if (not offers or not isinstance(quotes, list) or len(offers) != len(quotes)
            or costs.get("observation_id") != observation.get("observation_id")
            or any("quote" in offer for offer in offers)):
        return []
    by_id = {quote["offer_id"]: quote for quote in quotes}
    if len(by_id) != len(quotes) or {offer["id"] for offer in offers} != by_id.keys():
        return []
    return [(offer, by_id[offer["id"]]) for offer in offers]


def _inline_quotes(observation, costs):
    matched = _matched_quotes(observation, costs)
    for offer, quote in matched:
        offer["quote"] = quote
        del quote["offer_id"]
        for field in ("label", "kind", "effects"):
            if field in offer and field in quote and same_value(offer[field], quote[field]):
                del quote[field]
        if "price" in offer and "cash_cost" in quote and same_value(offer["price"], quote["cash_cost"]):
            del offer["price"]
    if matched:
        del costs["offers"]


def _deduplicate_receipt(observation, memory):
    receipt = observation.get("last_action")
    if not isinstance(receipt, dict) or not receipt:
        return
    for frame in memory.get("frames", []):
        if ("observed_result_ref" not in frame and "observed_result" in frame
                and same_value(frame["observed_result"], receipt)):
            del frame["observed_result"]
            frame["observed_result_ref"] = LAST_ACTION_REF


def compact_context(content):
    """Only this complete request owns references; helper pages stay self-contained.

    Compare canonical values before audit fields are removed or IDs translated.
    Similar receipts from distinct events must not become false duplicates.
    """
    view = deepcopy(content)
    observation = view["observation"]
    _inline_quotes(observation, view.get("current_costs", {}))
    _deduplicate_receipt(observation, view.get("working_memory") or {})
    return view
