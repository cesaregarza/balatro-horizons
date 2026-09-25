"""Counting response for tests whose subject is the generation transport."""

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
        return receive(request)

    return wrapped
