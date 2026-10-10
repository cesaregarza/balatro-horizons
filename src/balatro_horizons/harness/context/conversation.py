"""One immutable provider prefix and append-only results per game decision."""

import json
from copy import deepcopy
from dataclasses import replace

from balatro_horizons.harness.context.build import (
    _allowed_tools,
    _helper_status,
    context_bound,
)
from balatro_horizons.harness.context.memory import maintenance
from balatro_horizons.harness.context.model_view import same_value
from balatro_horizons.harness.failures import HarnessFailure

CONTEXT_POLICY = "append_only_decision_v1"
WIRE_POLICIES = {
    "openai": "openai_responses_v1",
    "anthropic": "anthropic_messages_v1",
}
PARTIAL_UPDATE_FIELDS = ("remaining_budget", "helper_status", "notebook_maintenance")


class DecisionConversation:
    """Seal model-facing values once; local validation sees current allowances.

    This object is local to _decision, never serialized into a checkpoint. The
    raw provider_request journal remains the exact record of each wire payload.
    """

    def __init__(self, context):
        from balatro_horizons.harness.transport import canonical_messages

        self.initial = deepcopy(context)
        self.initial.model_references = context.model_references
        self.initial.provider_initial_content = canonical_messages(context)[0]["content"]
        view = json.loads(self.initial.provider_initial_content)
        self.last_delivered = {key: view[key] for key in (
            "run_notebook", "permitted_tools", "helper_status", "notebook_maintenance",
        )}
        self.last_delivered["remaining_budget"] = view["observation"]["remaining_budget"]
        self.exchanges = []

    def deliver(self, exchanges, *, notebook, helper_remaining, helper_count,
                provider_calls, provider_attempts, byte_limit):
        ctx = replace(
            self.initial,
            observation=deepcopy(self.initial.observation),
            run_notebook=deepcopy(notebook),
            helper_status=_helper_status(helper_remaining),
            allowed_tools=_allowed_tools(self.initial.tools, self.initial.observation,
                                         helper_remaining),
        )
        ctx.observation["remaining_budget"].update(
            provider_calls=provider_calls, helper_calls_this_decision=helper_count,
            helper_calls_remaining=helper_remaining, as_of_provider_attempt=provider_attempts,
        )
        metadata = {
            "policy": CONTEXT_POLICY, "cleared": [],
            "loaded_exchange_indices": list(range(len(exchanges))),
        }
        ctx.context_delivery = metadata
        ctx.observation["retrieval_context"] = deepcopy(metadata)
        maintenance(ctx)
        self._append(exchanges, ctx, provider_attempts)
        size = context_bound(ctx, self.exchanges)
        if size > byte_limit:
            raise HarnessFailure(
                "LOCAL_CONTEXT_LIMIT", stage="helper_followup", request_bytes=size,
                byte_limit=byte_limit, retained_helper_results=len(self.exchanges),
            )
        ctx.context_bytes_upper_bound = size
        return ctx, deepcopy(self.exchanges)

    def _append(self, exchanges, ctx, provider_attempts):
        # Detect accidental mutation of already accepted native turns or results.
        if len(exchanges) < len(self.exchanges):
            raise HarnessFailure("DECISION_TRANSCRIPT_CHANGED", stage="helper_followup")
        for old, current in zip(self.exchanges, exchanges, strict=False):
            canonical = {key: value for key, value in old.items() if key != "model_result"}
            if canonical != current:
                raise HarnessFailure("DECISION_TRANSCRIPT_CHANGED", stage="helper_followup")
        for exchange in exchanges[len(self.exchanges):]:
            sealed = deepcopy(exchange)
            sealed["model_result"] = ctx.model_references.project({
                "result": exchange["result"],
            })
            sealed["model_result"]["context_update"] = self._update(ctx, provider_attempts)
            self.exchanges.append(sealed)

    def _update(self, ctx, provider_attempts):
        current = ctx.model_references.project({
            "remaining_budget": ctx.observation["remaining_budget"],
            "permitted_tools": ctx.allowed_tools, "helper_status": ctx.helper_status,
            "run_notebook": ctx.run_notebook, "notebook_maintenance": ctx.notebook_maintenance,
        })
        update = {"as_of_provider_attempt": provider_attempts}
        for key, value in current.items():
            previous = self.last_delivered[key]
            if same_value(value, previous):
                continue
            # Fixed-shape status objects merge by field; a changed notebook is
            # a replacement, so deleted note keys cannot survive in the viewer.
            update[key] = ({name: item for name, item in value.items()
                            if name not in previous or not same_value(item, previous[name])}
                           if key in PARTIAL_UPDATE_FIELDS else value)
        self.last_delivered = deepcopy(current)
        return update
