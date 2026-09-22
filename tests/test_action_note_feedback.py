"""Bind a saved edit to one action, never to another action or a later rewrite."""

from copy import deepcopy

import pytest
from test_harness_memory import WorkingScript, annotated, note, play, public_event, select

from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.action_notes import ActionNoteLink
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.context.memory import RunNotebook
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending


def events():
    envelope = {"observation_id": 0, "action": {"type": "cash_out"}}
    operation = public_event("agent_operation", {"operation": {
        "kind": "action", "envelope": envelope,
        "note_update": {"key": "plan", "text": "I expect cash to increase"}}})
    mutation, _ = RunNotebook().propose("set_run_note", "plan", "I expect cash to increase")
    saved = public_event("run_note", mutation)
    committed = public_event("action_commit", ActionEnvelope.model_validate(envelope).model_dump(mode="json"))
    return operation, saved, committed


def test_requires_both_saved_edit_and_matching_commit_and_releases_only_once():
    operation, saved, committed = events()
    link = ActionNoteLink()
    assert link.consume(operation) is None
    assert link.consume(committed) is None  # Proposal was never saved.
    assert link.consume(saved) is None
    assert link.consume(committed) is None  # Mutation has no matching proposal.
    link.consume(operation)
    link.consume(saved)
    receipt = link.consume(committed)
    assert receipt["text"] == "I expect cash to increase"
    assert receipt["note_event_id"] == saved["event_id"]
    assert receipt["operation_event_id"] == operation["event_id"]
    assert link.consume(committed) is None


@pytest.mark.parametrize("mismatch", ["episode", "decision", "action", "text", "malformed", "helper"])
def test_never_borrows_a_note_from_unrelated_or_unvalidated_events(mismatch):
    operation, saved, committed = events()
    if mismatch == "episode":
        saved["episode_id"] = "b" * 32
    elif mismatch == "decision":
        saved["observation_id"] = 1
    elif mismatch == "action":
        committed["payload"]["action"] = {"type": "leave_shop"}
    elif mismatch == "text":
        saved["payload"]["text"] = "A different write"
    elif mismatch == "malformed":
        operation["payload"]["operation"]["envelope"] = None
    else:
        operation["payload"]["operation"] = {"kind": "set_run_note", "key": "plan",
                                             "text": "I expect cash to increase"}
    link = ActionNoteLink()
    for event in (operation, saved):
        link.consume(event)
    assert link.consume(committed) is None


@pytest.mark.parametrize("kind", ["action_rejected", "action_status_unknown", "harness_failure"])
def test_failure_cannot_turn_a_saved_edit_into_a_later_action_receipt(kind):
    operation, saved, committed = events()
    link = ActionNoteLink()
    link.consume(operation)
    link.consume(saved)
    link.consume(public_event(kind, {}))
    assert link.consume(committed) is None


def test_later_helper_rewrite_does_not_replace_the_preaction_edit(store, config):
    policy = WorkingScript(annotated(select, text="I expect the blind to start"),
                           note("plan", "The blind started; now play"), play)
    spending = Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )
    result = Runner(store, config, FakeGame(), policy, spending).run()
    original = policy.contexts[1]["previous_action_outcome"]["recorded_note_update"]
    after_helper = policy.contexts[2]
    assert after_helper["run_notebook"]["entries"]["plan"] == "The blind started; now play"
    assert after_helper["previous_action_outcome"]["recorded_note_update"] == original
    assert original["text"] == "I expect the blind to start"
    assert policy.contexts[3]["previous_action_outcome"]["recorded_note_update"] is None
    assert result["committed_actions"] == 2


def test_paired_edit_survives_request_pruning_without_copying_arbitrary_fields(store, config):
    policy = WorkingScript(annotated(select))
    spending = Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )
    result = Runner(store, config, FakeGame(), policy, spending).run()
    checkpoint = read_checkpoint(store, result["episode_id"], 1)
    observation = Observation.model_validate(checkpoint["observation"])
    memory = deepcopy(checkpoint["working_memory"])
    memory["frames"][0]["helpers"] = [{"result": "x" * 6000}]
    ctx, _ = decision_context(observation, [], working_memory=memory)
    smaller, _ = decision_context(observation, [], working_memory=memory,
                                   byte_limit=ctx["context_bytes_upper_bound"] - 500)
    assert smaller["working_memory"]["frames"] == []
    assert smaller["previous_action_outcome"]["recorded_note_update"]["text"] == "Keep this conclusion"
    assert smaller["previous_action_outcome"] == ctx["previous_action_outcome"]
    operation, saved, committed = events()
    operation["payload"]["private"] = saved["payload"]["private"] = "PRIVATE_SENTINEL"
    link = ActionNoteLink()
    link.consume(operation)
    link.consume(saved)
    assert "PRIVATE_SENTINEL" not in str(link.consume(committed))
