"""Build the public model context in the delivery order used by the harness."""

import json
from copy import deepcopy

from balatro_horizons.config import (
    CONTEXT_FRAMING_BYTES,
    CONTEXT_SETTINGS_BYTES,
    DEFAULT_REQUEST_BYTE_LIMIT,
    RETAINED_HELPER_RESULTS,
    ROOT,
)
from balatro_horizons.harness.context.game_quotes import current_costs
from balatro_horizons.harness.context.memory import (
    RunNotebook,
    WorkingMemory,
    maintenance,
    trim_oldest,
)
from balatro_horizons.harness.context.present import focused_observation
from balatro_horizons.harness.context.render import render_prompt, rules_kernel, tool_catalog
from balatro_horizons.harness.contract import Context
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.tool_interface import ACTION_MODELS


def build_context(
    observation, exchanges=(), *, byte_limit=DEFAULT_REQUEST_BYTE_LIMIT,
    skills=(), frozen=None, notebook=None, helper_remaining=None, working_memory=None,
    references=None,
):
    """Build the public fields in source order, then fit the delivery window."""
    original, focused, omitted = _base_fields(observation)
    prompt = _prompt(frozen)
    kernel = _kernel(skills, frozen)
    tools = _tools(skills, frozen)
    notes, history = _memory_fields(notebook, working_memory)
    outcome, deliver_outcome = _previous_outcome(original, history, frozen)
    allowed = _allowed_tools(tools, original, helper_remaining)
    helper_status = _helper_status(helper_remaining)
    costs = current_costs(original)
    context = _assembled_context(original, prompt, kernel, tools, costs, focused,
                                 omitted, notes, history, outcome, deliver_outcome,
                                 allowed, helper_status)
    if references is not None:
        context.model_references = references
    context.model_references.observe(original)
    return _bounded_delivery(context, exchanges, skills, byte_limit)


def _base_fields(observation):
    original = observation.model_dump(mode="json")
    focused, omitted = focused_observation(original)
    focused.pop("memory", None)
    return original, focused, omitted


def _prompt(frozen):
    if frozen is not None:
        return frozen["prompt_utf8"].strip()
    return render_prompt((ROOT / "configs/prompts/harness.txt").read_bytes()).decode().strip()


def _kernel(skills, frozen):
    return frozen["rules_kernel"] if frozen is not None else rules_kernel(skills)


def _tools(skills, frozen):
    return deepcopy(frozen["tool_catalog"]) if frozen is not None else tool_catalog(skills)


def _memory_fields(notebook, working_memory):
    notes = deepcopy(notebook if notebook is not None else RunNotebook().view())
    history = deepcopy(
        working_memory if working_memory is not None else WorkingMemory().view()
    )
    return notes, history


def _previous_outcome(original, history, frozen):
    from balatro_horizons.harness.outcomes import VERSION, previous_action_outcome

    deliver = frozen is None or frozen.get("memory_policy", {}).get("action_outcome") == VERSION
    return (previous_action_outcome(original, history) if deliver else None), deliver


def _allowed_tools(tools, original, helper_remaining):
    allowed = [
        tool["name"] for tool in tools
        if tool["name"] not in ACTION_MODELS
        or tool["name"] in original["available_action_types"]
    ]
    if helper_remaining == 0:
        allowed = [name for name in allowed if name in ACTION_MODELS or name == "abort_run"]
    return allowed


HELPER_EXHAUSTED_MESSAGE = (
    "Helper allowance exhausted. Choose a permitted gameplay action or abort_run."
)


def _helper_status(helper_remaining):
    return {
        "remaining": helper_remaining,
        "message": (
            HELPER_EXHAUSTED_MESSAGE
            if helper_remaining == 0 else
            "Helper calls share this allowance. Action-attached note_update uses no helper call."
        ),
    }


def _assembled_context(
    original, prompt, kernel, tools, costs, focused, omitted, notes, history,
    outcome, deliver_outcome, allowed, helper_status,
):
    return Context(
        prompt=prompt, rules_kernel=kernel, interface_version="harness", tools=tools,
        current_costs=costs, observation=focused, omitted_event_ids=omitted,
        run_notebook=notes, working_memory=history,
        previous_action_outcome=outcome, deliver_previous_action_outcome=deliver_outcome,
        allowed_tools=allowed, helper_status=helper_status,
    )


def _bounded_delivery(context, exchanges, skills, byte_limit):
    # The old context() fitted the base request before decision_context() added
    # current-decision results and the skill-delivery marker. Keep both passes.
    context, _ = working_context(context, [], byte_limit)
    # Either exchanges or a skill catalog makes this a decision delivery;
    # a plain context() call stops after the base pass.
    if exchanges or skills:
        if skills:
            context.skill_catalog_delivery = "names_and_descriptions"
        return working_context(context, exchanges, byte_limit)
    return context, []


def context(observation, **kwargs):
    """Existing single-request entry point for the typed builder."""
    return build_context(observation, **kwargs)[0]


def decision_context(observation, exchanges, **kwargs):
    """Follow-up entry point retaining the same byte-bound sequence."""
    return build_context(observation, exchanges, **kwargs)


def context_bound(context, exchanges):
    """Use the larger provider serialization so pruning is provider-independent.

    Fixed settings/framing padding covers request bytes outside those payloads.
    """
    from balatro_horizons.harness.transport import context_payload

    return (
        max(len(json.dumps(context_payload(context, exchanges, provider),
                           ensure_ascii=False).encode())
            for provider in ("openai", "anthropic"))
        + CONTEXT_FRAMING_BYTES + CONTEXT_SETTINGS_BYTES
    )


def working_context(context, exchanges, byte_limit):
    return _DeliveryWindow(context, exchanges, byte_limit).fit()


class _DeliveryWindow:
    def __init__(self, context, exchanges, byte_limit):
        self.context = context
        self.delivered = deepcopy(exchanges)
        self.indices = list(range(len(self.delivered)))
        self.cleared = []
        self.byte_limit = byte_limit
        self.stage = "helper_followup" if exchanges else "initial_request"

    def loaded_positions(self):
        return [i for i, exchange in enumerate(self.delivered)
                if not (isinstance(exchange.get("result"), dict)
                        and exchange["result"].get("context_cleared") is True)]

    def clear_at(self, position):
        exchange = self.delivered[position]
        operation = exchange["operation"]
        reference = {key: operation[key] for key in
                     ("kind", "key", "name", "section", "offset", "byte_offset", "limit",
                      "decision_id", "episode_id") if key in operation}
        if operation.get("kind") in ("set_run_note", "delete_run_note"):
            # A note acknowledgment is disposable. Its mutation must never be replayed
            # as a reload hint; the latest state remains in the dynamic notebook.
            reference = {"source": "run_notebook", "mutation_already_recorded": True}
        self.cleared.append({"exchange_index": self.indices[position], "reload": reference})
        if exchange.get("provider_turn"):
            exchange["result"] = {"context_cleared": True, "game_advanced": False,
                                  "reload": reference}
        else:
            self.delivered.pop(position)
            self.indices.pop(position)

    def update_metadata(self):
        if self.context.working_memory is not None:
            maintenance(self.context, len(self.loaded_positions()))
        metadata = {
            "policy": "bounded_recent_results_provider_turns_retained",
            "cleared": deepcopy(self.cleared),
            "loaded_exchange_indices": [self.indices[i] for i in self.loaded_positions()],
        }
        self.context.observation["retrieval_context"] = metadata
        self.context.context_delivery = deepcopy(metadata)

    def shrink_one(self):
        # Product precedence: oldest frame, loaded helper, public event, then failure.
        # test_context_shrink_precedence.py pins every fallback in this order.
        loaded = self.loaded_positions()
        if trim_oldest(self.context):
            self.update_metadata()
        elif loaded:
            self.clear_at(loaded[0])
            self.update_metadata()
        elif self.context.observation["recent_public_events"]:
            self.context.omitted_event_ids.append(
                self.context.observation["recent_public_events"].pop(0)["event_id"]
            )
        else:
            raise HarnessFailure(
                "LOCAL_CONTEXT_LIMIT", stage=self.stage,
                request_bytes=context_bound(self.context, self.delivered),
                byte_limit=self.byte_limit,
                retained_provider_turns=sum(bool(e.get("provider_turn")) for e in self.delivered),
                retained_helper_results=len(loaded),
            )

    def fit(self):
        while len(self.loaded_positions()) > RETAINED_HELPER_RESULTS:
            self.clear_at(self.loaded_positions()[0])
        self.update_metadata()
        while context_bound(self.context, self.delivered) > self.byte_limit:
            self.shrink_one()
        self.update_metadata()
        self.context.context_bytes_upper_bound = context_bound(self.context, self.delivered)
        return self.context, self.delivered
