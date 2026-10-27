"""Layered requests: run-invariant text is stated once in the frozen prefix.

From least to most changeable: L0 is the frozen prompt, rules kernel and tools; L1 the
run configuration appended to the kernel; L2 the decision message sealed for the whole
decision; L3 the follow-up exchanges, whose counters arrive in context_update. The first
cache breakpoint ends L0+L1; providers place the follow-up breakpoints themselves.

The fields below cannot change within a protocol, so the kernel states them and each
decision message omits them while they hold exactly a stated value. Any other value is
still delivered where it occurs.
"""

import json

from balatro_horizons.contracts import LastAction, Objective
from balatro_horizons.harness.context.game_quotes import COSTS_VERSION, quote_note
from balatro_horizons.harness.context.memory import (
    NOTEBOOK_MAINTENANCE_MESSAGE,
    WORKING_MEMORY_MEANING,
    working_memory_policy,
)
from balatro_horizons.harness.context.memory import VERSION as NOTEBOOK_VERSION
from balatro_horizons.harness.context.present import presentation
from balatro_horizons.harness.outcomes import VERSION as OUTCOME_VERSION
from balatro_horizons.harness.tool_interface import INSPECT_SECTIONS

CONTEXT_POLICY = "layered_decision_v1"
PUBLIC_CONTRACT_VERSION = "1.1"  # what the public projection writes for every new observation
HELPER_MESSAGE = "Helper calls share this allowance. Action-attached note_update uses no helper call."
HELPER_EXHAUSTED_MESSAGE = (
    "Helper allowance exhausted. Choose a permitted gameplay action or abort_run."
)
RECEIPT_VERSION = LastAction.model_fields["version"].default


def _stated():
    policy = working_memory_policy()
    limits = ("version", "max_decisions", "max_bytes", "helpers_per_decision", "max_helper_bytes")
    return (
        (("observation", "objective"), (Objective().model_dump(mode="json"),)),
        (("observation", "public_contract_version"), (PUBLIC_CONTRACT_VERSION,)),
        (("observation", "details_available"), (list(INSPECT_SECTIONS),)),
        (("observation", "presentation"),
         (presentation(hand_levels_deferred=False), presentation(hand_levels_deferred=True))),
        (("observation", "last_action", "version"), (RECEIPT_VERSION,)),
        (("working_memory", "frames", "*", "observed_result", "version"), (RECEIPT_VERSION,)),
        (("current_costs", "version"), (COSTS_VERSION,)),
        (("current_costs", "note"), (quote_note(),)),
        (("run_notebook", "version"), (NOTEBOOK_VERSION,)),
        (("helper_status", "message"), (HELPER_MESSAGE, HELPER_EXHAUSTED_MESSAGE)),
        *((("working_memory", key), (policy[key],)) for key in limits),
        (("working_memory", "meaning"), (WORKING_MEMORY_MEANING,)),
        (("notebook_maintenance", "message"), (NOTEBOOK_MAINTENANCE_MESSAGE,)),
        (("previous_action_outcome", "version"), (OUTCOME_VERSION,)),
    )


STATED = _stated()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def request_conventions():
    """The L0 restatement, one line per omitted field."""
    lines = [
        "Fixed request fields. Every request omits each of these while it holds a value shown "
        "here; a field that is present overrides it. presentation.deferred_sections also lists "
        "hand_levels outside SELECTING_HAND, and helper_status.message is the exhausted text "
        "exactly when helper_status.remaining is 0."
    ]
    for path, values in STATED:
        dotted = ".".join(path).replace(".*", "[]")
        lines.append(f"- {dotted}: " + " or ".join(encode(value) for value in values))
    return "\n".join(lines)


def omit_stated(view):
    """Remove stated fields that hold a stated value, in place; anything else stays."""
    for path, values in STATED:
        _omit(view, path, {json.dumps(value, sort_keys=True) for value in values})
    return view


def _omit(node, path, values):
    head, rest = path[0], path[1:]
    if head == "*":
        for item in node if isinstance(node, list) else ():
            _omit(item, rest, values)
    elif isinstance(node, dict) and head in node:
        if rest:
            _omit(node[head], rest, values)
        elif json.dumps(node[head], sort_keys=True) in values:
            del node[head]
