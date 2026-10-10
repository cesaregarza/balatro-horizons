"""Only unsent initial context may shrink; follow-ups never rewrite history."""

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


def test_initial_shrink_precedence_is_memory_then_public_event(monkeypatch):
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
    delivered, retained = focused.working_context(context, [], 1)

    assert seen[:3] == [(1, 0, 1), (0, 0, 1), (0, 0, 0)]
    assert delivered["working_memory"]["request_pruned_decisions"] == 1
    assert delivered["omitted_event_ids"] == ["event-1"]
    assert retained == []


def test_shrink_fails_after_initial_fallbacks_are_exhausted(monkeypatch):
    monkeypatch.setattr(focused, "context_bound", lambda _ctx, _exchanges: 100)
    context = context_with([], [])
    with pytest.raises(HarnessFailure) as error:
        focused.working_context(context, [], 1)
    assert error.value.code == "LOCAL_CONTEXT_LIMIT"


def test_followup_limit_preserves_memory_results_and_events(monkeypatch):
    monkeypatch.setattr(focused, "context_bound", lambda _ctx, _exchanges: 100)
    context = context_with([{"episode_id": "parent", "decision_id": 1}],
                           [{"event_id": "event-1"}])
    exchanges = [{"operation": {"kind": "rules", "key": "index"}, "result": {"text": "kept"}}]
    with pytest.raises(HarnessFailure, match="LOCAL_CONTEXT_LIMIT"):
        focused.working_context(context, exchanges, 1)
    assert len(context.working_memory["frames"]) == 1
    assert context.observation["recent_public_events"] == [{"event_id": "event-1"}]
    assert exchanges[0]["result"] == {"text": "kept"}
