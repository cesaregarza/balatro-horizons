"""Golden delivery contract for the single surviving harness interface."""

import hashlib
import json
from copy import deepcopy

from balatro_horizons.config import ROOT
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.runner import Runner


class GoldenProvider:
    paid = False
    name = "golden-fake"

    def __init__(self):
        self.contexts = []

    def decide(self, context, exchanges):
        self.contexts.append(deepcopy(context))
        if len(self.contexts) > 3:
            return {"kind": "abort", "reason": "golden delivery captured"}
        observation = context["observation"]
        if "select_blind" in observation["available_action_types"]:
            action = {
                "type": "select_blind",
                "blind_id": observation["state"]["revealed_blinds"][0]["id"],
            }
        elif "play_hand" in observation["available_action_types"]:
            action = {
                "type": "play_hand",
                "card_ids": [card["id"] for card in observation["state"]["hand"][:3]],
            }
        else:
            action = {"type": "cash_out"}
        return {
            "kind": "action",
            "envelope": {"observation_id": observation["observation_id"], "action": action},
        }


def digest(value):
    return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}


def test_harness_delivery_is_byte_stable(store, config):
    provider = GoldenProvider()
    result = Runner(store, config, FakeGame("HARNESS_GOLDEN"), provider).run()
    rejected = [
        event["payload"]
        for event in store.events(result["episode_id"])
        if event["type"] == "action_rejected"
    ]
    assert result["committed_actions"] == 3, (
        result.get("outcome"),
        result.get("reason"),
        rejected,
    )
    contexts = provider.contexts[:3]
    prompt = contexts[0]["prompt"].encode()
    tools = json.dumps(contexts[0]["tools"], ensure_ascii=False, separators=(",", ":")).encode()
    assert all(context["prompt"].encode() == prompt for context in contexts)
    assert all(
        json.dumps(context["tools"], ensure_ascii=False, separators=(",", ":")).encode() == tools
        for context in contexts
    )
    actual = {
        "prompt_utf8": digest(prompt),
        "tool_catalog_json": digest(tools),
        "context_keys": [sorted(context) for context in contexts],
    }
    golden = json.loads((ROOT / "tests/fixtures/harness-delivery.json").read_text())
    assert actual == golden, json.dumps(actual, indent=2)
