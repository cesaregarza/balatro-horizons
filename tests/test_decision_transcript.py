"""Shared delivery conformance; all provider responses here are synthetic."""

import json
from copy import deepcopy

import pytest
from test_boundary import project
from test_provider_continuations import model

from balatro_horizons.config import Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.context.conversation import DecisionConversation
from balatro_horizons.harness.context.memory import RunNotebook
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.transport import DirectProvider


def delivery(conversation, exchanges, *, attempts=0, notebook=None, byte_limit=262144):
    return conversation.deliver(
        exchanges, notebook=notebook or RunNotebook().view(),
        helper_remaining=8 - len(exchanges), helper_count=len(exchanges),
        provider_calls=100 - attempts, provider_attempts=attempts, byte_limit=byte_limit,
    )


def response(provider, index):
    if provider == "openai":
        return {"status": "completed", "output": [
            {"type": "reasoning", "id": f"rs_{index}", "summary": [],
             "encrypted_content": f"opaque_{index}"},
            {"type": "function_call", "call_id": f"call_{index}", "status": "completed",
             "name": "calculate", "arguments": '{"expression":"1+1"}'},
        ]}
    return {"stop_reason": "tool_use", "content": [
        {"type": "thinking", "thinking": "Public summary", "signature": f"opaque_{index}"},
        {"type": "tool_use", "id": f"call_{index}", "name": "calculate",
         "input": {"expression": "1+1"}},
    ]}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_five_helper_prefix_is_immutable_with_notebook_budget_and_reference_updates(provider):
    ctx, _ = decision_context(project(FakeGame().observe_private()), [], helper_remaining=8)
    conversation = DecisionConversation(ctx)
    book, exchanges, snapshots = RunNotebook(), [], []
    policy = DirectProvider(model(provider), Limits())
    field = "input" if provider == "openai" else "messages"
    try:
        for index in range(6):
            if index:
                mutation, _ = book.propose("set_run_note", "plan", f"updated {index}")
                book.apply(mutation)
            current, delivered = delivery(conversation, exchanges, attempts=index * 2,
                                          notebook=book.view())
            body = policy.request(current, delivered)
            if snapshots:
                old = snapshots[-1]
                assert body[field][:len(old[field])] == old[field]
                assert body["tools"] == old["tools"]
                if provider == "anthropic":
                    assert body["system"] == old["system"]
                latest = delivered[-1]["model_result"]["context_update"]
                assert latest["run_notebook"]["entries"] == {"plan": f"updated {index}"}
                assert latest["remaining_budget"]["provider_calls"] == 100 - index * 2
                assert latest["as_of_provider_attempt"] == index * 2
                assert "retrieval_context" not in latest
                assert "message" not in latest["helper_status"]
            assert "provider_initial_content" not in dict(current)
            snapshots.append(deepcopy(body))
            raw = policy.parse(response(provider, index))
            exchanges.append({"operation": raw, "result": {"result": f"answer {index}"},
                              "tool_call": deepcopy(policy.last_tool_call),
                              "provider_turn": deepcopy(policy.last_provider_turn)})
        wire = json.dumps(snapshots[-1])
        assert all(f"answer {index}" in wire for index in range(5))
        assert "context_cleared" not in wire
        assert "retrieval_context" not in wire
        assert "next_helper_may_clear_older_results" not in wire
    finally:
        policy.client.close()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("edit_once", [False, True])
def test_context_deltas_compare_to_last_delivery_including_initial_notebook(provider, edit_once):
    book = RunNotebook()
    mutation, _ = book.propose("set_run_note", "plan", "INITIAL-NOTE")
    book.apply(mutation)
    ctx, _ = decision_context(project(FakeGame().observe_private()), [],
                              helper_remaining=8, notebook=book.view())
    conversation, exchanges = DecisionConversation(ctx), []
    policy = DirectProvider(model(provider), Limits())
    field = "input" if provider == "openai" else "messages"
    previous = None
    try:
        for index in range(5):
            if edit_once and index == 1:
                mutation, _ = book.propose("set_run_note", "plan", "UPDATED-NOTE")
                book.apply(mutation)
            current, delivered = delivery(conversation, exchanges, attempts=index,
                                          notebook=book.view())
            body = policy.request(current, delivered)
            if previous is not None:
                assert body[field][:len(previous[field])] == previous[field]
                update = delivered[-1]["model_result"]["context_update"]
                assert update["as_of_provider_attempt"] == index
                assert update["remaining_budget"]["provider_calls"] == 100 - index
                assert update["helper_status"] == {"remaining": 8 - index}
                assert "permitted_tools" not in update
                assert "notebook_maintenance" not in update
                assert ("run_notebook" in update) is (edit_once and index == 1)
                if index > 1 or not edit_once:
                    assert "-NOTE" not in json.dumps(update)
            previous = deepcopy(body)
            raw = policy.parse(response(provider, index))
            exchanges.append({"operation": raw, "result": {"result": "2"},
                              "provider_turn": deepcopy(policy.last_provider_turn)})
    finally:
        policy.client.close()


def test_notebook_delta_is_a_replacement_and_can_return_to_initial_contents():
    book = RunNotebook()
    ctx, _ = decision_context(project(FakeGame().observe_private()), [], notebook=book.view())
    conversation, exchanges = DecisionConversation(ctx), []
    for kind in ("set_run_note", "delete_run_note"):
        mutation, result = book.propose(kind, "plan", "new note")
        book.apply(mutation)
        exchanges.append({"operation": {"kind": kind}, "result": result})
        _, delivered = delivery(conversation, exchanges, notebook=book.view())
        update = delivered[-1]["model_result"]["context_update"]
        assert update["run_notebook"]["entries"] == book.view()["entries"]


@pytest.mark.parametrize("field", ["result", "provider_turn", "operation"])
def test_mutating_an_accepted_exchange_is_rejected(field):
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    conversation = DecisionConversation(ctx)
    exchange = {"operation": {"kind": "arithmetic", "expression": "1+1"},
                "result": {"result": "2"}, "provider_turn": {"items": []}}
    delivery(conversation, [exchange])
    changed = deepcopy(exchange)
    changed[field] = {"changed": True}
    with pytest.raises(HarnessFailure, match="DECISION_TRANSCRIPT_CHANGED"):
        delivery(conversation, [changed])


def test_transmitted_initial_snapshot_and_results_cannot_shrink_under_pressure():
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    conversation = DecisionConversation(ctx)
    first = conversation.initial.provider_initial_content
    exchange = {"operation": {"kind": "arithmetic", "expression": "1+1"},
                "result": {"result": "x" * 2000}}
    with pytest.raises(HarnessFailure, match="LOCAL_CONTEXT_LIMIT"):
        delivery(conversation, [exchange], byte_limit=1)
    assert conversation.initial.provider_initial_content == first
    assert conversation.exchanges[0]["result"] == exchange["result"]


def test_new_decision_has_no_previous_native_continuation():
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    old = DecisionConversation(ctx)
    delivery(old, [{"operation": {"kind": "invalid"}, "result": {"error": "NO_OPERATION"},
                    "provider_turn": {"items": [{"signature": "private-prior-decision"}]}}])
    new = DecisionConversation(ctx)
    fresh, exchanges = delivery(new, [])
    assert exchanges == []
    assert "private-prior-decision" not in fresh.provider_initial_content
