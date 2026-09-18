"""V7 continuity, edits and temporal isolation; synthetic game / mocked APIs only."""

import json
from copy import deepcopy

import pytest
from test_notebook_harness import Script, note, play, select

from balatro_horizons.agents.focused import context_bound
from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import context_payload
from balatro_horizons.agents.tool_interface import ACTION_MODELS, decode_tool, stable_tools
from balatro_horizons.agents.working_memory import (
    WorkingMemory,
    restore_working_memory,
    size,
)
from balatro_horizons.config import WORKING_MEMORY_BYTES, WORKING_MEMORY_DECISIONS
from balatro_horizons.contracts import Observation
from balatro_horizons.engine.certification import read_checkpoint, verify_checkpoint
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.review.branches import prepare_branch
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner


class WorkingScript(Script):
    interface = "tools_v7"


def annotated(action, key="plan", text="Keep this conclusion"):
    def operation(ctx):
        return {**action(ctx), "note_update": {"key": key, "text": text}}
    return operation


def test_recent_actions_results_and_helpers_survive_without_note_writes(store, config):
    policy = WorkingScript({"kind": "arithmetic", "expression": "7*6"}, select,
                           {"kind": "inspect_page", "section": "hand", "offset": 0}, play)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 2 and result["reason"] == "AGENT_ABORT"
    ctx = policy.contexts[-1]
    frames = ctx["working_memory"]["frames"]
    assert [f["action"]["type"] for f in frames] == ["select_blind", "play_hand"]
    assert frames[0]["helpers"][0]["result"] == {"result": "42"}
    assert frames[1]["helpers"][0]["operation"]["section"] == "hand"
    assert frames[-1]["observed_result"] == ctx["observation"]["last_action"]
    assert frames[-1]["recorded_decision_note"].startswith("Recorded note")
    assert ctx["run_notebook"]["entries"] == {}
    outcome = ctx["previous_action_outcome"]
    assert outcome["action_type"] == "play_hand"
    assert outcome["recorded_decision_note"] == frames[-1]["recorded_decision_note"]
    assert outcome["from_observation_id"] == frames[-1]["decision_id"]
    assert policy.exchanges[-1] == []  # No provider-native replay across actions.
    assert "Maintain a concise" in ctx["prompt"]


def test_action_attached_notes_need_no_helper_and_delete_is_visible(store, config):
    config.budgets.max_helper_calls_per_decision = 0
    policy = WorkingScript(annotated(select), annotated(play, text=None))
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 2
    assert policy.contexts[1]["run_notebook"]["entries"] == {"plan": "Keep this conclusion"}
    assert policy.contexts[2]["run_notebook"]["entries"] == {}
    assert all(c["helper_status"]["remaining"] == 0 for c in policy.contexts)
    events = store.events(result["episode_id"])
    assert len([e for e in events if e["type"] == "run_note"]) == 2
    assert not any(e["type"] == "helper_result" for e in events)
    assert read_checkpoint(store, result["episode_id"], 0)["run_notebook"]["revision"] == 0
    assert read_checkpoint(store, result["episode_id"], 1)["run_notebook"]["revision"] == 1


@pytest.mark.parametrize("edit,code", [
    ({"key": "x", "text": "x" * 4096}, "RUN_NOTEBOOK_LIMIT"),
    ({"key": " ", "text": "bad"}, "INVALID_RUN_NOTE_KEY"),
    ({"key": "missing", "text": None}, "RUN_NOTE_NOT_FOUND"),
])
def test_bad_edit_rejects_entire_submission_without_execution(store, config, edit, code):
    policy = WorkingScript(lambda ctx: {**select(ctx), "note_update": edit}, select)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 1
    assert policy.contexts[0]["observation"]["observation_id"] == policy.contexts[1]["observation"]["observation_id"]
    assert policy.exchanges[1][-1]["result"]["error"] == code
    assert not any(e["type"] == "run_note" for e in store.events(result["episode_id"]))


def test_bad_game_action_does_not_write_valid_attached_note(store, config):
    def invalid(ctx):
        raw = annotated(select)(ctx)
        raw["envelope"]["action"]["blind_id"] = "invalid"
        return raw
    policy = WorkingScript(invalid, select)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 1
    assert policy.contexts[1]["run_notebook"]["revision"] == 0


def test_durable_attached_note_survives_native_failure_without_claiming_success(store, config):
    from balatro_horizons.engine.native import NativeRejected

    class RejectedGame(FakeGame):
        def apply_public_action(self, *args):
            raise NativeRejected("test rejection")

    result = Runner(store, config, RejectedGame(), WorkingScript(annotated(select))).run()
    events = store.events(result["episode_id"])
    assert result["committed_actions"] == 0
    assert any(e["type"] == "run_note" for e in events)
    assert not any(e["type"] == "action_commit" for e in events)
    assert read_checkpoint(store, result["episode_id"], 0)["run_notebook"]["revision"] == 0


def test_note_storage_failure_prevents_game_action(store, config, monkeypatch):
    original = store.append
    def append(eid, kind, payload, **kwargs):
        if kind == "run_note":
            raise OSError("test storage failure")
        return original(eid, kind, payload, **kwargs)
    monkeypatch.setattr(store, "append", append)
    runner = Runner(store, config, FakeGame(), WorkingScript(annotated(select)))
    result = runner.run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["committed_actions"] == 0 and runner.notebook.entries == {}


def public_event(kind, payload, decision=0):
    return {"type": kind, "payload": payload, "episode_id": "a" * 32,
            "event_id": f"{kind}-{decision}", "observation_id": decision}


def memory_fixture(count=5):
    memory = WorkingMemory()
    for i in range(count):
        memory.consume(public_event("observation", {
            "phase": "SHOP", "state": {"progress": {"ante": i}, "resources": {"money": i}},
            "last_action": {"action_type": "reroll_shop", "after": i}}, i))
        for n in range(5):
            memory.consume(public_event("helper_result", {"operation": {"kind": "arithmetic", "expression": f"{n}+1"},
                "result": {"result": str(n+1)}}, i))
        memory.consume(public_event("action_commit", {"action": {"type": "reroll_shop"},
            "decision_note": f"Note {i}"}, i))
    memory.observe({"last_action": {"action_type": "reroll_shop", "after": count}})
    return memory


def test_retention_bounds_drop_whole_frames_and_private_events_are_ignored():
    memory = memory_fixture()
    view = memory.view()
    assert [f["decision_id"] for f in view["frames"]] == [2, 3, 4]
    assert view["omitted_decisions"] == 2
    assert all(len(f["helpers"]) == 3 and f["omitted_helpers"] == 2 for f in view["frames"])
    for kind in ("provider_response", "provider_request", "agent_context", "evaluator_fixture"):
        memory.consume(public_event(kind, {"secret": "PRIVATE_SENTINEL"}))
    assert memory.view() == view
    memory.consume(public_event("observation", {"state": {}, "phase": "SHOP"}))
    memory.consume(public_event("helper_result", {"operation": {"kind": "rules", "key": "core"},
        "result": {"text": "猫" * 9000}}))
    memory.consume(public_event("action_commit", {"action": {"type": "cash_out"}}))
    memory.observe({"last_action": {"lots": "x" * WORKING_MEMORY_BYTES}})
    assert size(memory.view()["frames"]) <= WORKING_MEMORY_BYTES
    assert len(memory.view()["frames"]) <= WORKING_MEMORY_DECISIONS


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_dynamic_history_and_action_notes_keep_fixed_prefix(provider, store, config):
    policy = WorkingScript(annotated(select), play)
    Runner(store, config, FakeGame(), policy).run()
    first, last = [context_payload(ctx, [], provider, "tools_v7") for ctx in (policy.contexts[0], policy.contexts[-1])]
    assert first["tools"] == last["tools"]
    assert (first["input"][0] == last["input"][0]) if provider == "openai" else (first["system"] == last["system"])
    messages = last.get("input", last.get("messages"))
    view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
    assert len(view["working_memory"]["frames"]) == 2
    assert view["run_notebook"]["entries"] == {"plan": "Keep this conclusion"}
    assert view["previous_action_outcome"] == policy.contexts[-1]["previous_action_outcome"]
    assert view["previous_action_outcome"]["action_type"] == "play_hand"
    for definition in last["tools"]:
        if definition["name"] in ACTION_MODELS:
            schema = definition.get("parameters", definition.get("input_schema"))
            assert "note_update" in schema["required"] and "memory_update" not in schema["properties"]
            if definition["name"] in ("buy", "use_consumable", "choose_pack"):
                guidance = schema["properties"]["target_ids"]["description"]
                assert "list order does not move them" in guidance
                assert "Death converts the left selected card into the right selected card" in guidance
    args = {"observation_id": 0, "note_update": {"key": "k", "text": "v"}}
    assert decode_tool("cash_out", args, interface="tools_v7")["note_update"] == args["note_update"]
    with pytest.raises(ValueError, match="UNAVAILABLE_TOOL_ARGUMENT"):
        decode_tool("cash_out", args, interface="tools_v6")


def test_unfrozen_context_has_same_target_guidance_without_changing_legacy(store, config):
    policy = WorkingScript(select)
    result = Runner(store, config, FakeGame(), policy).run()
    checkpoint = read_checkpoint(store, result["episode_id"], 0)
    canonical = Observation.model_validate(checkpoint["observation"])
    ctx, _ = decision_context(canonical, [], interface="tools_v7")
    legacy, _ = decision_context(canonical, [], interface="tools_v6")
    old_definitions = {definition["name"]: definition for definition in stable_tools()}
    frozen_definitions = {definition["name"]: definition for definition in policy.contexts[0]["tools"]}
    for definition in ctx["tools"]:
        if definition["name"] in ("buy", "use_consumable", "choose_pack"):
            assert definition == frozen_definitions[definition["name"]]
    for definition in legacy["tools"]:
        if definition["name"] in ("buy", "use_consumable", "choose_pack"):
            assert definition["parameters"]["properties"]["target_ids"] == (
                old_definitions[definition["name"]]["parameters"]["properties"]["target_ids"]
            )
    assert "previous_action_outcome" not in legacy


@pytest.mark.parametrize("name,selection", [
    ("buy", {"offer_id": "offer", "mode": "buy_and_use"}),
    ("use_consumable", {"consumable_id": "death"}),
    ("choose_pack", {"offer_id": "offer"}),
])
def test_target_guidance_never_reorders_a_submitted_selection(name, selection):
    args = {"observation_id": 4, "target_ids": ["right_card", "left_card"],
            "note_update": None, "decision_note": None, **selection}
    action = decode_tool(name, args, interface="tools_v7")["envelope"]["action"]
    assert action["target_ids"] == ["right_card", "left_card"]
    assert args["target_ids"] == action["target_ids"]


def test_context_budget_prunes_history_before_live_helpers_and_notebook(store, config):
    result = Runner(store, config, FakeGame(), WorkingScript(select)).run()
    observation = Observation.model_validate(read_checkpoint(store, result["episode_id"], 1)["observation"])
    memory = memory_fixture().view()
    ctx, exchanges = decision_context(observation, [], interface="tools_v7", working_memory=memory)
    assert ctx["notebook_maintenance"]["oldest_decision_leaves_after_action"]["decision_id"] == 2
    bound = context_bound(ctx, exchanges)
    smaller, _ = decision_context(observation, [], interface="tools_v7", working_memory=memory, byte_limit=bound-500)
    assert len(smaller["working_memory"]["frames"]) < 3
    assert smaller["previous_action_outcome"] == ctx["previous_action_outcome"]
    assert smaller["working_memory"]["request_pruned_decisions"] > 0
    assert smaller["context_bytes_upper_bound"] <= bound-500
    assert memory["frames"][0]["decision_id"] == 2  # Source never mutated.
    helpers = [{"operation": {"kind": "arithmetic", "expression": "1+1"}, "result": {"result": "2"}}] * 3
    ctx, _ = decision_context(observation, helpers, interface="tools_v7", working_memory=memory)
    assert ctx["notebook_maintenance"]["next_helper_may_clear_older_results"]


def test_branch_restores_exact_predecision_context_and_rejects_tampering(store, config):
    root = Runner(store, config, FakeGame(), WorkingScript(
        {"kind": "arithmetic", "expression": "7*6"}, annotated(select),
        {"kind": "rules", "key": "FUTURE_PARENT"}, play)).run()["episode_id"]
    assert verify_checkpoint(store, config, root, 1)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, root, 1, "agent_continue")
    child_policy = WorkingScript(play, note("later", "FUTURE_CHILD"))
    result = Runner(store, config, FakeGame(), child_policy).run(eid=child, resume=checkpoint, history_prefix=prefix)
    assert result["reason"] == "AGENT_ABORT"
    view = child_policy.contexts[0]["working_memory"]
    assert view == checkpoint["working_memory"]
    assert view["frames"][0]["episode_id"] == root
    assert "FUTURE_PARENT" not in json.dumps(view)
    assert verify_checkpoint(store, config, child, 1)["status"] == "passed"
    grandchild, snapshot, ancestors = prepare_branch(store, config, child, 1, "agent_continue")
    grand = WorkingScript()
    Runner(store, config, FakeGame(), grand).run(eid=grandchild, resume=snapshot, history_prefix=ancestors)
    assert grand.contexts[0]["working_memory"] == view
    assert "FUTURE_CHILD" not in json.dumps(grand.contexts[0])
    corrupt = deepcopy(snapshot["working_memory"])
    corrupt["frames"][0]["helpers"][0]["result"] = {"result": "999"}
    with pytest.raises(ValueError, match="WORKING_MEMORY_SNAPSHOT_MISMATCH"):
        restore_working_memory(corrupt, ancestors, snapshot["observation"])


def test_prospective_review_and_export_keep_current_edit_behind_action_reveal(store, config):
    eid = Runner(store, config, FakeGame(), WorkingScript(annotated(select, text="CURRENT_EDIT_SENTINEL"), play)).run()["episode_id"]
    review = ReviewService(store)
    token = review.open(eid)["review_token"]
    assert "CURRENT_EDIT_SENTINEL" not in json.dumps(review.view(token))
    review.advance(token)
    assert "CURRENT_EDIT_SENTINEL" in json.dumps(review.view(token)["action_events"])
    exported = episode_export(store, eid)
    assert any(e["type"] == "run_note" for e in exported["events"])
    assert "working_memory" in json.dumps(exported)
