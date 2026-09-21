"""The same explicit context and operation schemas for every playing policy."""

import ast
import operator
from copy import deepcopy
from decimal import Decimal

from balatro_horizons.agents.skills import discovery, read_guide
from balatro_horizons.agents.tool_interface import ACTION_MODELS, stable_tools
from balatro_horizons.config import DEFAULT_REQUEST_BYTE_LIMIT, MAX_ARITHMETIC_NODES, ROOT
from balatro_horizons.harness.contract import ActionResult as ActionResult
from balatro_horizons.harness.contract import Operation as Operation
from balatro_horizons.harness.contract import Rules as Rules

KERNEL = "The objective is the ordinary Ante 8 native run win. Hand scores resolve in native order. Discards consume a discard; playing consumes a hand. Money, remaining hands, Jokers, consumables and their order carry native effects. Use only visible state and permitted history. Hidden identities and future draws are unknown. Rules lookup accepts a visible item name, a rules key, or index to list frozen keys."


def context(
    observation,
    *,
    byte_limit=DEFAULT_REQUEST_BYTE_LIMIT,
    skills=(),
    frozen=None,
    notebook=None,
    helper_remaining=None,
    working_memory=None,
):
    from balatro_horizons.agents.costs import current_costs
    from balatro_horizons.agents.focused import focused_observation, focused_tools, working_context
    from balatro_horizons.agents.notebook import RunNotebook, notebook_tools
    from balatro_horizons.agents.working_memory import WorkingMemory

    original_observation = observation.model_dump(mode="json")
    tools = notebook_tools(
        focused_tools(stable_tools(skills=skills, target_guidance=True)), action_notes=True
    )
    result = {
        "prompt": (ROOT / "configs/prompts/harness.txt").read_text().strip(),
        "rules_kernel": (
            "Resolve scores in native order. Read the available skills and linked rules when useful."
            if skills
            else KERNEL
        )
        + discovery(skills),
        "interface_version": "harness",
        "tools": tools,
        "current_costs": current_costs(original_observation),
    }
    if frozen is not None:
        result["prompt"] = frozen["prompt_utf8"].strip()
        result["rules_kernel"] = frozen["rules_kernel"]
        result["tools"] = deepcopy(frozen["tool_catalog"])
    result["observation"], result["omitted_event_ids"] = focused_observation(
        original_observation
    )
    result["observation"].pop("memory", None)
    result["run_notebook"] = deepcopy(
        notebook if notebook is not None else RunNotebook().view()
    )
    result["working_memory"] = deepcopy(
        working_memory if working_memory is not None else WorkingMemory().view()
    )
    from balatro_horizons.agents.outcomes import VERSION, previous_action_outcome

    if frozen is None or frozen.get("memory_policy", {}).get("action_outcome") == VERSION:
        result["previous_action_outcome"] = previous_action_outcome(
            original_observation, result["working_memory"]
        )
    result["allowed_tools"] = [
        tool["name"]
        for tool in result["tools"]
        if tool["name"] not in ACTION_MODELS
        or tool["name"] in original_observation["available_action_types"]
    ]
    if helper_remaining == 0:
        result["allowed_tools"] = [
            name for name in result["allowed_tools"]
            if name in ACTION_MODELS or name == "abort_run"
        ]
    result["helper_status"] = {
        "remaining": helper_remaining,
        "message": (
            "Helper allowance exhausted. Choose a permitted gameplay action or abort_run."
            if helper_remaining == 0
            else "Helper calls share this allowance. Action-attached note_update uses no helper call."
        ),
    }
    return working_context(result, [], byte_limit)[0]


def decision_context(
    observation,
    exchanges,
    *,
    byte_limit=DEFAULT_REQUEST_BYTE_LIMIT,
    skills=(),
    frozen=None,
    notebook=None,
    helper_remaining=None,
    working_memory=None,
):
    """Place retrieved current-state sections once, preserving every value read."""
    ctx = context(
        observation,
        byte_limit=byte_limit,
        skills=skills,
        frozen=frozen,
        notebook=notebook,
        helper_remaining=helper_remaining,
        working_memory=working_memory,
    )
    if skills:
        ctx["skill_catalog_delivery"] = "names_and_descriptions"
    from balatro_horizons.agents.focused import working_context

    return working_context(ctx, exchanges, byte_limit)


def arithmetic(expression):
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > MAX_ARITHMETIC_NODES:
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


def helper(operation, events, rules, observation=None):
    if operation.kind == "action_result":
        from balatro_horizons.agents.action_results import retrieve_action_result

        return retrieve_action_result(operation, events, observation)
    from balatro_horizons.agents.focused import focused_helper

    if observation is not None:
        result = focused_helper(operation, events, rules, observation)
        if result is not None:
            return result
    elif operation.kind in ("inspect_page", "history", "history_detail"):
        raise ValueError("CURRENT_OBSERVATION_REQUIRED")
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
