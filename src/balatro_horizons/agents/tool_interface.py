"""Named tools and a compact presentation of the already-masked public state."""

from copy import deepcopy
from typing import get_args

from balatro_horizons.contracts import Action

VERSION = "tools_v2"
NAMED_INTERFACES = ("tools_v2", "tools_v3", "tools_v4", "tools_v5")
FOCUSED_INTERFACES = ("tools_v3", "tools_v4", "tools_v5")
STABLE_TOOL_INTERFACES = ("tools_v4", "tools_v5")
CONTINUATION_INTERFACES = ("tools_v5",)
INSPECT_SECTIONS = (
    "hand",
    "jokers",
    "consumables",
    "revealed_blinds",
    "offers",
    "hand_levels",
    "persistent_effects",
    "owned_vouchers",
    "pending_tags",
    "public_deck_knowledge",
    "resources",
    "progress",
    "recent_public_events",
    "action_constraints",
    "last_action",
)
ACTION_MODELS = {
    model.model_fields["type"].annotation.__args__[0]: model for model in get_args(Action)
}
DESCRIPTIONS = {
    "select_blind": "Fight the current available blind. This does not award its skip tag. Other revealed blinds cannot be selected yet.",
    "skip_blind": "Skip the current available non-boss blind and take its skip tag. Advance to the next blind without a shop.",
    "play_hand": "Play selected hand cards. Their order is the current hand order; use reorder to change it. Consumes one hand.",
    "discard": "Discard selected hand cards and draw replacements. Consumes one discard.",
    "reorder": "Set the complete order of an area. Include every current ID exactly once.",
    "buy": "Buy a shop offer. Acquire keeps it; buy_and_use uses a consumable immediately with any required hand targets.",
    "sell": "Sell an owned Joker or consumable for its displayed sell price.",
    "use_consumable": "Use an owned consumable, selecting hand targets when required by the item.",
    "reroll_shop": "Pay the displayed reroll cost and replace the shop's rerollable offers.",
    "reroll_boss": "Pay the displayed boss reroll cost and replace the revealed boss.",
    "choose_pack": "Choose an offer from the open pack. Consumables are used now with any required hand targets.",
    "skip_pack": "Leave the open pack, forfeiting any remaining choices.",
    "leave_shop": "Leave this shop and continue to blind selection.",
    "cash_out": "Collect the completed round payout and continue.",
}


def tool(name, description, properties):
    # Flat schemas: no discriminated unions or duplicated action envelopes.
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def clean_schema(schema):
    if isinstance(schema, dict):
        return {k: clean_schema(v) for k, v in schema.items() if k not in ("title", "default")}
    if isinstance(schema, list):
        return [clean_schema(v) for v in schema]
    return schema


def tools_for(observation, *, skills=()):
    result = []
    for name in observation["available_action_types"]:
        props = clean_schema(ACTION_MODELS[name].model_json_schema()["properties"])
        props.pop("type")
        props = {
            "observation_id": {"type": "integer", "enum": [observation["observation_id"]]},
            **props,
        }
        state = observation["state"]
        hand_ids = [c["id"] for c in state["hand"]]
        owned_ids = [c["id"] for c in state["jokers"] + state["consumables"]]
        for field, ids in (
            ("card_ids", hand_ids),
            ("target_ids", hand_ids),
            ("ordered_ids", hand_ids + owned_ids),
        ):
            if field in props and ids:
                props[field]["items"]["enum"] = ids
        for field, ids in (
            ("offer_id", [c["id"] for c in state["offers"]]),
            ("owned_id", owned_ids),
            ("consumable_id", [c["id"] for c in state["consumables"]]),
        ):
            if field in props and ids:
                props[field]["enum"] = ids
        if name in ("select_blind", "skip_blind"):
            current = observation["state"]["revealed_blinds"][0]
            props["blind_id"]["enum"] = [current["id"]]
            props["blind_id"]["description"] = "Current blind: " + current["label"]
        if name in ("play_hand", "discard"):
            limits = observation["action_constraints"][name]
            props["card_ids"].update(minItems=limits["min_cards"], maxItems=limits["max_cards"])
            props["card_ids"]["description"] = (
                "Unique IDs from the current hand. Required IDs: "
                + ", ".join(limits.get("required_ids", []))
            )
        if name == "reorder":
            areas = observation["action_constraints"]["reorder"]["areas"]
            props["area"]["enum"] = areas
            props["ordered_ids"]["items"]["enum"] = [
                card["id"] for area in areas for card in state[area]
            ]
        props["memory_update"] = {
            "type": ["string", "null"],
            "maxLength": 4096,
            "description": "Replace your notes for later decisions, or null to keep them. No other private memory carries forward.",
        }
        props["decision_note"] = {
            "type": ["string", "null"],
            "maxLength": 512,
            "description": "Optional decision note; null is fine. Not graded.",
        }
        result.append(tool(name, DESCRIPTIONS[name], props))
    result.extend(
        [
            tool(
                "inspect_state",
                "Read selected sections of the current public state without changing the game. Includes full deck-view and recent-history details omitted from the compact view.",
                {
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(INSPECT_SECTIONS)},
                        "minItems": 1,
                        "maxItems": len(INSPECT_SECTIONS),
                    },
                },
            ),
            tool(
                "read_rules",
                "Look up frozen game rules by visible item name or key. Use index to list available keys. Does not advance the game.",
                {"key": {"type": "string"}},
            ),
            tool(
                "read_history",
                "Read past public observations and committed actions. Offset is zero-based from the start of the run; never includes future events.",
                {
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            ),
            tool(
                "calculate",
                "Evaluate arithmetic using numbers, parentheses, +, -, *, / and %. Does not advance the game.",
                {"expression": {"type": "string", "maxLength": 256}},
            ),
            tool(
                "abort_run",
                "End this run voluntarily. No restart is available.",
                {"reason": {"type": "string", "maxLength": 256}},
            ),
        ]
    )
    if skills:
        result.append(
            tool(
                "read_skill",
                "Read a Balatro skill. Follow next_key with read_rules for more text. Does not advance the game.",
                {"name": {"type": "string", "enum": [item["name"] for item in skills]}},
            )
        )
    return result


def stable_tools(*, skills=()):
    """Frozen catalog; current observation constraints determine legality.

    No observation IDs, card handles, phases or other changing data belong here.
    Providers render tools before messages, so those values invalidate the prefix.
    """
    actions = []
    for name, model in ACTION_MODELS.items():
        props = clean_schema(model.model_json_schema()["properties"])
        props.pop("type")
        actions.append(
            tool(
                name,
                DESCRIPTIONS[name],
                {
                    "observation_id": {"type": "integer", "minimum": 0},
                    **props,
                    "memory_update": {"type": ["string", "null"], "maxLength": 4096},
                    "decision_note": {"type": ["string", "null"], "maxLength": 512},
                },
            )
        )
    return actions + tools_for({"available_action_types": []}, skills=skills)


def decode_tool(name, arguments, *, interface="tools_v2"):
    if not isinstance(arguments, dict):
        raise ValueError("TOOL_ARGUMENTS_MUST_BE_OBJECT")
    args = deepcopy(arguments)
    if set(args) & {"type", "kind", "envelope"}:
        raise ValueError("UNEXPECTED_TOOL_WRAPPER")
    if name == "inspect_state" and interface in FOCUSED_INTERFACES:
        if "section" not in args or "sections" in args:
            raise ValueError("EXPECTED_PAGED_INSPECTION")
        return {**args, "kind": "inspect_page"}
    if name in ACTION_MODELS:
        envelope = {
            k: args.pop(k)
            for k in ("observation_id", "memory_update", "decision_note")
            if k in args
        }
        return {"kind": "action", "envelope": {**envelope, "action": {**args, "type": name}}}
    kinds = {
        "inspect_state": "inspect",
        "read_rules": "rules",
        "read_skill": "skill",
        "read_history": "history",
        "read_history_detail": "history_detail",
        "calculate": "arithmetic",
        "abort_run": "abort",
    }
    if name not in kinds:
        raise ValueError("UNKNOWN_TOOL")
    return {**args, "kind": kinds[name]}


def compact_observation(observation):
    result = deepcopy(observation)
    result["public_contract_version"] = result["schema_version"]
    # Full canonical observations stay in the journal and inspect_state. Remove
    # bookkeeping from automatic context, not current effects or boss information.
    for key in ("schema_version", "episode_id", "public_state_hash"):
        result.pop(key)
    result["state"].pop("public_deck_knowledge")
    omitted = [e["event_id"] for e in result["recent_public_events"][:-2]]
    result["recent_public_events"] = result["recent_public_events"][-2:]
    result["details_available"] = list(INSPECT_SECTIONS)
    if result["phase"] == "BLIND_SELECT" and result["state"]["revealed_blinds"]:
        result["current_blind_id"] = result["state"]["revealed_blinds"][0]["id"]
    return result, omitted


def inspect_state(operation, observation):
    public = observation.model_dump(mode="json")
    return {
        "observation_id": observation.observation_id,
        "sections": {
            section: deepcopy(
                public[section]
                if section in ("recent_public_events", "action_constraints", "last_action")
                else public["state"][section]
            )
            for section in operation.sections
        },
        "game_advanced": False,
    }
