"""The same explicit context and operation schemas for every playing policy."""

import ast
import json
import operator
from copy import deepcopy
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from balatro_horizons.agents.skills import discovery, read_guide
from balatro_horizons.agents.tool_interface import (
    ACTION_MODELS,
    FOCUSED_INTERFACES,
    INSPECT_SECTIONS,
    NAMED_INTERFACES,
    compact_observation,
    stable_tools,
    tools_for,
)
from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope, StrictModel


class Submit(StrictModel):
    kind: Literal["action"]
    envelope: ActionEnvelope


class Rules(StrictModel):
    kind: Literal["rules"]
    key: str


class Skill(StrictModel):
    kind: Literal["skill"]
    name: str = Field(pattern=r"^balatro-[a-z0-9-]+$", max_length=64)


class History(StrictModel):
    kind: Literal["history"]
    offset: int = Field(ge=0)
    limit: int = Field(default=10, ge=1, le=20)


class Arithmetic(StrictModel):
    kind: Literal["arithmetic"]
    expression: str = Field(max_length=256)


class Abort(StrictModel):
    kind: Literal["abort"]
    reason: str = Field(max_length=256)


class Inspect(StrictModel):
    kind: Literal["inspect"]
    sections: list[Literal[*INSPECT_SECTIONS]] = Field(
        min_length=1, max_length=len(INSPECT_SECTIONS)
    )


class InspectPage(StrictModel):
    kind: Literal["inspect_page"]
    section: Literal[*INSPECT_SECTIONS]
    offset: int = Field(default=0, ge=0)


class HistoryDetail(StrictModel):
    kind: Literal["history_detail"]
    offset: int = Field(ge=0)
    byte_offset: int = Field(default=0, ge=0)


Operation = TypeAdapter(
    Annotated[
        Submit
        | Rules
        | History
        | Arithmetic
        | Abort
        | Inspect
        | Skill
        | InspectPage
        | HistoryDetail,
        Field(discriminator="kind"),
    ]
)
LegacyOperation = TypeAdapter(
    Annotated[Submit | Rules | History | Arithmetic | Abort, Field(discriminator="kind")]
)
TOOL = {
    "name": "operate",
    "description": "Perform exactly one action, request permitted help, or abort.",
    "parameters": {"type": "object", **LegacyOperation.json_schema()},
}
PROMPT = (ROOT / "configs/prompts/core.txt").read_text().strip()
KERNEL = "The objective is the ordinary Ante 8 native run win. Hand scores resolve in native order. Discards consume a discard; playing consumes a hand. Money, remaining hands, Jokers, consumables and their order carry native effects. Use only visible state and permitted history. Hidden identities and future draws are unknown. Rules lookup accepts a visible item name, a rules key, or index to list frozen keys."


def context(
    observation, *, byte_limit=32768, interface="operate_v1", skills=(), skill_descriptions=True
):
    observation = observation.model_dump(mode="json")
    original_observation = deepcopy(observation)
    result = {
        "prompt": PROMPT,
        "rules_kernel": (
            "Resolve scores in native order. Read the available skills and linked rules when useful."
            if skills
            else KERNEL
        )
        + discovery(skills, interface, descriptions=skill_descriptions),
        "observation": observation,
        "tool": TOOL,
        "omitted_event_ids": [],
    }
    if interface in NAMED_INTERFACES:
        result["interface_version"] = interface
        result["tools"] = (
            stable_tools(skills=skills)
            if interface == "tools_v4"
            else tools_for(observation, skills=skills)
        )
        result.pop("tool")
        result["observation"], result["omitted_event_ids"] = compact_observation(observation)
        observation = result["observation"]
        result["prompt"] = (ROOT / "configs/prompts/tools-v2.txt").read_text().strip()
        if interface in FOCUSED_INTERFACES:
            from balatro_horizons.agents.focused import (
                focused_observation,
                focused_tools,
                working_context,
            )

            result["observation"], result["omitted_event_ids"] = focused_observation(
                # Use the canonical source, not the already-compacted v2 view.
                original_observation
            )
            result["tools"] = focused_tools(result["tools"])
            result["prompt"] = (
                (ROOT / "configs/prompts" / (interface.replace("_", "-") + ".txt"))
                .read_text()
                .strip()
            )
            if interface == "tools_v4":
                result["allowed_tools"] = [
                    t["name"]
                    for t in result["tools"]
                    if t["name"] not in ACTION_MODELS
                    or t["name"] in original_observation["available_action_types"]
                ]
                result["observation"]["presentation"]["version"] = interface
            return working_context(result, [], byte_limit)[0]
    # UTF-8 bytes plus conservative framing allowance is an upper bound on text tokens.
    while len(json.dumps(result, ensure_ascii=False).encode()) + 4096 > byte_limit:
        events = observation["recent_public_events"]
        if not events:
            raise ValueError("REQUIRED_CONTEXT_EXCEEDS_LIMIT")
        result["omitted_event_ids"].append(events.pop(0)["event_id"])
    return result


def decision_context(
    observation, exchanges, *, byte_limit=32768, interface="operate_v1", skills=()
):
    """Place retrieved current-state sections once, preserving every value read."""
    ctx = context(
        observation,
        byte_limit=byte_limit,
        interface=interface,
        skills=skills,
        skill_descriptions=not exchanges or interface == "tools_v4",
    )
    if skills:
        ctx["skill_catalog_delivery"] = (
            "names_and_descriptions"
            if not exchanges or interface == "tools_v4"
            else (
                "names_in_tool_schema" if interface in NAMED_INTERFACES else "names_in_rules_kernel"
            )
        )
    if interface in FOCUSED_INTERFACES:
        from balatro_horizons.agents.focused import working_context

        return working_context(ctx, exchanges, byte_limit)
    if interface != "tools_v2":
        return ctx, exchanges
    effective = [deepcopy(exchange) for exchange in exchanges]
    restored_events = set()
    projected = []
    for index, exchange in enumerate(effective):
        result = exchange["result"]
        if exchange["operation"].get("kind") != "inspect" or "sections" not in result:
            continue
        references = {}
        for section, value in result["sections"].items():
            if section not in INSPECT_SECTIONS:
                raise ValueError("UNKNOWN_PUBLIC_INSPECTION_SECTION")
            if section in ("recent_public_events", "action_constraints"):
                ctx["observation"][section] = deepcopy(value)
                references[section] = "observation." + section
            else:
                ctx["observation"]["state"][section] = deepcopy(value)
                references[section] = "observation.state." + section
            if section == "recent_public_events":
                restored_events.update(e["event_id"] for e in value)
        exchange["result"] = {
            "observation_id": result["observation_id"],
            "game_advanced": False,
            "sections_read": references,
            "delivery": "Full returned values are present once at the referenced observation paths.",
        }
        projected.append({"exchange_index": index, "sections": list(references)})
    ctx["omitted_event_ids"] = [e for e in ctx["omitted_event_ids"] if e not in restored_events]
    older = {row["exchange_index"] for row in projected[:-1]}
    effective = [exchange for i, exchange in enumerate(effective) if i not in older]
    ctx["observation"]["inspected_sections"] = sorted(
        {s for row in projected for s in row["sections"]}
    )
    ctx["inspection_delivery"] = {
        "policy": "current_sections_once",
        "exchanges": projected,
        "coalesced_exchange_indices": sorted(older),
    }
    return ctx, effective


def arithmetic(expression):
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 64:
        raise ValueError("EXPRESSION_TOO_COMPLEX")
    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
    }

    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = Decimal(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = walk(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in ops:
            result = ops[type(node.op)](walk(node.left), walk(node.right))
        else:
            raise ValueError("UNSUPPORTED_ARITHMETIC")
        if not result.is_finite() or abs(result) > Decimal("1e100"):
            raise ValueError("ARITHMETIC_MAGNITUDE_LIMIT")
        return result

    return str(walk(tree.body))


def helper(operation, events, rules, observation=None, *, interface="operate_v1"):
    if interface in FOCUSED_INTERFACES:
        from balatro_horizons.agents.focused import focused_helper

        if observation is None:
            raise ValueError("CURRENT_OBSERVATION_REQUIRED")
        result = focused_helper(operation, events, rules, observation)
        if result is not None:
            return result
    if operation.kind == "inspect":
        from balatro_horizons.agents.tool_interface import inspect_state

        if observation is None:
            raise ValueError("CURRENT_OBSERVATION_REQUIRED")
        return inspect_state(operation, observation)
    if operation.kind == "arithmetic":
        return {"result": arithmetic(operation.expression)}
    if operation.kind == "skill":
        item = next((s for s in rules.get("skills", []) if s["name"] == operation.name), None)
        if item is None:
            return {"error": "UNKNOWN_SKILL", "game_advanced": False}
        return read_guide(rules, item["key"])
    if operation.kind == "rules":
        entries = rules.get("entries", rules)
        key = rules.get("aliases", {}).get(operation.key.casefold(), operation.key)
        if key.startswith("guide/") and (key.partition("#offset=")[0] in entries):
            return read_guide(rules, key)
        if key == "index":
            return {"reference": "frozen_rules", "keys": sorted(entries)}
        result = {
            "key": operation.key,
            "entry": entries.get(key),
            "reference": "frozen_rules",
        }
        return result
    if operation.kind == "history":
        public = [
            e
            for e in events
            if e["type"] in ("observation", "action_commit", "automatic_transition")
        ]
        page = public[operation.offset : operation.offset + operation.limit]
        return {
            "events": page,
            "next_offset": operation.offset + len(page)
            if operation.offset + len(page) < len(public)
            else None,
        }
    raise ValueError("NOT_A_HELPER")
