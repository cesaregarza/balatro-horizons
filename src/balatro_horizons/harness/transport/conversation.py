"""Common public content and immutable exchange envelopes for native serializers."""

import json
from copy import deepcopy

from balatro_horizons.harness.context.model_view import compact_context, truncate_summaries


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_messages(ctx):
    frozen = getattr(ctx, "provider_initial_content", None)
    if frozen is not None:
        return [{"role": "user", "content": frozen}]
    content = {"observation": ctx.observation, "omitted_event_ids": ctx.omitted_event_ids}
    if "current_costs" in ctx:
        content["current_costs"] = ctx.current_costs
    content.update(
        run_notebook=ctx.run_notebook, permitted_tools=ctx.allowed_tools,
        helper_status=ctx.helper_status, working_memory=ctx.working_memory,
        notebook_maintenance=ctx.notebook_maintenance,
    )
    if "previous_action_outcome" in ctx:
        content["previous_action_outcome"] = ctx.previous_action_outcome
    view = ctx.model_references.project(compact_context(content))
    return [{"role": "user", "content": encode(truncate_summaries(view))}]


def exchange_result(ctx, exchange):
    if "model_result" in exchange:
        return deepcopy(exchange["model_result"])
    # Legacy direct fixtures have no decision owner to seal their envelope.
    return ctx.model_references.project(exchange["result"])


def native_items(exchange, provider):
    turn = exchange.get("provider_turn")
    if not isinstance(turn, dict) or turn.get("provider") != provider:
        return None
    items = turn.get("items")
    return deepcopy(items) if isinstance(items, list) else None
