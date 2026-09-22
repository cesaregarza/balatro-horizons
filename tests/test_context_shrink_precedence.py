"""Characterize the existing request-shrink order before the context move."""

import pytest

from balatro_horizons.harness.context import build as focused
from balatro_horizons.harness.contract import Context
from balatro_horizons.harness.failures import HarnessFailure


def context_with(frames, events):
    return Context(
        prompt="", rules_kernel="", interface_version="harness", tools=[],
        current_costs={}, observation={"recent_public_events": events},
        omitted_event_ids=[], run_notebook={},
        working_memory={"frames": frames, "omitted_decisions": 0},
        allowed_tools=[], helper_status={},
    )


def test_shrink_precedence_is_memory_then_helper_then_public_event(monkeypatch):
    seen = []

    def bound(ctx, exchanges):
        state = (
            len(ctx["working_memory"]["frames"]),
            len(exchanges),
            len(ctx["observation"]["recent_public_events"]),
        )
        seen.append(state)
        return 100 if any(state) else 0

    monkeypatch.setattr(focused, "context_bound", bound)
    context = context_with(
        [{"episode_id": "parent", "decision_id": 1}], [{"event_id": "event-1"}]
    )
    exchanges = [{"operation": {"kind": "rules", "key": "index"},
                  "result": {"reference": "frozen_rules"}}]
    delivered, retained = focused.working_context(context, exchanges, 1)

    assert seen[:4] == [(1, 1, 1), (0, 1, 1), (0, 0, 1), (0, 0, 0)]
    assert delivered["working_memory"]["request_pruned_decisions"] == 1
    assert delivered["omitted_event_ids"] == ["event-1"]
    assert retained == []


def test_shrink_fails_only_after_all_four_fallbacks_are_exhausted(monkeypatch):
    monkeypatch.setattr(focused, "context_bound", lambda _ctx, _exchanges: 100)
    context = context_with([], [])
    with pytest.raises(HarnessFailure) as error:
        focused.working_context(context, [], 1)
    assert error.value.code == "LOCAL_CONTEXT_LIMIT"
