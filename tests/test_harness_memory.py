"""Harness continuity, edits and temporal isolation; synthetic game only."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from runner_support import episode_spending
from test_boundary import project
from test_harness_tools import config_for
from test_provider_continuations import model

from balatro_horizons.config import WORKING_MEMORY_BYTES, WORKING_MEMORY_DECISIONS
from balatro_horizons.contracts import Observation
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.evidence.certification import read_checkpoint, verify_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context_bound, decision_context
from balatro_horizons.harness.context.memory import (
    RunNotebook,
    WorkingMemory,
    fold_notebook,
    restore_notebook,
    restore_working_memory,
    size,
)
from balatro_horizons.harness.contract import ActionResult
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.tool_interface import ACTION_MODELS, decode_tool
from balatro_horizons.harness.transport import DirectProvider, context_payload
from balatro_horizons.workbench.branches import prepare_branch
from balatro_horizons.workbench.service import WorkbenchService as ReviewService


def select(ctx):
    observation = ctx["observation"]
    return {
        "kind": "action",
        "envelope": {
            "observation_id": observation["observation_id"],
            "action": {
                "type": "select_blind",
                "blind_id": observation["state"]["revealed_blinds"][0]["id"],
            },
        },
    }


def play(ctx):
    observation = ctx["observation"]
    return {
        "kind": "action",
        "envelope": {
            "observation_id": observation["observation_id"],
            "decision_note": "Recorded note; not necessarily a prediction.",
            "action": {
                "type": "play_hand",
                "card_ids": [card["id"] for card in observation["state"]["hand"][:3]],
            },
        },
    }


def note(key, text):
    return {"kind": "set_run_note", "key": key, "text": text}


class WorkingScript:
    paid = False
    name = "heuristic"

    def __init__(self, *operations):
        self.operations = iter(operations)
        self.contexts = []
        self.exchanges = []

    def decide(self, ctx, exchanges):
        self.contexts.append(deepcopy(ctx))
        self.exchanges.append(deepcopy(exchanges))
        operation = next(self.operations, {"kind": "abort", "reason": "test finished"})
        return operation(ctx) if callable(operation) else operation


def annotated(action, key="plan", text="Keep this conclusion"):
    def operation(ctx):
        return {**action(ctx), "note_update": {"key": key, "text": text}}

    return operation


def test_notebook_unicode_accounting_updates_and_delete_are_atomic():
    book = RunNotebook(limit=10)
    change, _ = book.propose("set_run_note", "é", "猫" * 9)
    book.apply(change)
    original = book.snapshot()
    change, result = book.propose("set_run_note", "é", "猫" * 10)
    assert change is None and result["error"] == "RUN_NOTEBOOK_LIMIT"
    assert book.snapshot() == original
    for kind, key, value in (
        ("set_run_note", "é", "short"),
        ("set_run_note", "x", "ok"),
        ("delete_run_note", "é", None),
    ):
        change, _ = book.propose(kind, key, value)
        book.apply(change)
    assert book.entries == {"x": "ok"}
    assert book.propose("delete_run_note", "missing")[1]["error"] == "RUN_NOTE_NOT_FOUND"
    for key in ("", " ", "x\n", "a" * 65):
        assert book.propose("set_run_note", key, "text")[1]["error"] == "INVALID_RUN_NOTE_KEY"


def test_notebook_replay_rejects_missing_revisions_and_malformed_mutations():
    book = RunNotebook()
    change, _ = book.propose("set_run_note", "x", "y")
    for corrupted in (
        None,
        {**change, "revision": 2},
        {**change, "revision": True},
        {**change, "text": "different"},
        {**change, "extra": "unrecognized"},
    ):
        with pytest.raises(ValueError, match="RUN_NOTEBOOK_JOURNAL_MISMATCH"):
            book.apply(corrupted)
        assert book.entries == {} and book.revision == 0
    book.apply(change)
    with pytest.raises(ValueError, match="RUN_NOTEBOOK_JOURNAL_MISMATCH"):
        book.apply(change)


def test_helper_write_is_visible_immediately_and_survives_pruning_and_actions(store, config):
    policy = WorkingScript(
        note("scoring/pair", "one observed result"),
        *[{"kind": "arithmetic", "expression": "1+1"}] * 4,
        select,
        play,
    )
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["committed_actions"] == 2 and result["reason"] == "AGENT_ABORT"
    assert policy.contexts[0]["run_notebook"]["revision"] == 0
    for ctx in policy.contexts[1:]:
        assert ctx["run_notebook"]["entries"] == {"scoring/pair": "one observed result"}
        assert "memory" not in ctx["observation"]
    assert policy.contexts[5]["observation"]["retrieval_context"]["cleared"][0]["reload"] == {
        "source": "run_notebook", "mutation_already_recorded": True,
    }
    events = store.events(result["episode_id"])
    observations = [event for event in events if event["type"] == "observation"]
    assert observations[0]["payload"]["memory"] == ""
    assert observations[1]["payload"]["last_action"]["action_type"] == "select_blind"
    assert observations[2]["payload"]["last_action"]["action_type"] == "play_hand"
    assert fold_notebook(events).snapshot() == read_checkpoint(
        store, result["episode_id"], 2
    )["run_notebook"]


def test_repeated_exhausted_helpers_fail_without_substituting_game_action(store, config):
    config.budgets.max_helper_calls_per_decision = 0
    policy = WorkingScript(*[{"kind": "arithmetic", "expression": "1+1"}] * 3)
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["reason"] == "AGENT_PROTOCOL_FAILURE" and result["committed_actions"] == 0
    assert len(policy.contexts) == 3


def test_recent_actions_results_and_helpers_survive_without_note_writes(store, config):
    policy = WorkingScript({"kind": "arithmetic", "expression": "7*6"}, select,
                           {"kind": "inspect_page", "section": "hand", "offset": 0}, play)
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
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
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["committed_actions"] == 2
    assert policy.contexts[1]["run_notebook"]["entries"] == {"plan": "Keep this conclusion"}
    assert policy.contexts[2]["run_notebook"]["entries"] == {}
    written = policy.contexts[1]["previous_action_outcome"]["recorded_note_update"]
    deleted = policy.contexts[2]["previous_action_outcome"]["recorded_note_update"]
    assert (written["key"], written["text"], written["revision"]) == ("plan", "Keep this conclusion", 1)
    assert written["timing"] == "before_action_execution"
    assert (deleted["operation"], deleted["text"], deleted["revision"]) == ("delete_run_note", None, 2)
    assert all(c["helper_status"]["remaining"] == 0 for c in policy.contexts)
    events = store.events(result["episode_id"])
    assert len([e for e in events if e["type"] == "run_note"]) == 2
    assert written["note_event_id"] == next(e["event_id"] for e in events if e["type"] == "run_note")
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
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["committed_actions"] == 1
    assert policy.contexts[0]["observation"]["observation_id"] == policy.contexts[1]["observation"]["observation_id"]
    assert policy.exchanges[1][-1]["result"]["error"] == code
    assert not any(e["type"] == "run_note" for e in store.events(result["episode_id"]))
    assert policy.contexts[-1]["previous_action_outcome"]["recorded_note_update"] is None


def test_bad_game_action_does_not_write_valid_attached_note(store, config):
    def invalid(ctx):
        raw = annotated(select)(ctx)
        raw["envelope"]["action"]["blind_id"] = "invalid"
        return raw
    policy = WorkingScript(invalid, select)
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["committed_actions"] == 1
    assert policy.contexts[1]["run_notebook"]["revision"] == 0
    assert policy.contexts[-1]["previous_action_outcome"]["recorded_note_update"] is None


def test_durable_attached_note_survives_native_failure_without_claiming_success(store, config):
    from balatro_horizons.game.contract import NativeRejected

    class RejectedGame(FakeGame):
        def apply_public_action(self, *args):
            raise NativeRejected("test rejection")

    runner = Runner(
        store,
        config,
        RejectedGame(),
        WorkingScript(annotated(select)),
        episode_spending(store, config),
    )
    result = runner.run()
    events = store.events(result["episode_id"])
    assert result["committed_actions"] == 0
    assert any(e["type"] == "run_note" for e in events)
    assert not any(e["type"] == "action_commit" for e in events)
    assert runner.working_memory.view()["frames"] == []
    assert read_checkpoint(store, result["episode_id"], 0)["run_notebook"]["revision"] == 0


def test_note_storage_failure_prevents_game_action(store, config, monkeypatch):
    original = store.append
    def append(eid, kind, payload, **kwargs):
        if kind == "run_note":
            raise OSError("test storage failure")
        return original(eid, kind, payload, **kwargs)
    monkeypatch.setattr(store, "append", append)
    runner = Runner(
        store,
        config,
        FakeGame(),
        WorkingScript(annotated(select)),
        episode_spending(store, config),
    )
    result = runner.run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["committed_actions"] == 0 and runner.notebook.entries == {}


def test_failed_helper_note_journal_append_does_not_make_note_visible(store, config, monkeypatch):
    original = store.append

    def append(eid, kind, payload, **kwargs):
        if kind == "run_note":
            raise OSError("simulated storage failure")
        return original(eid, kind, payload, **kwargs)

    monkeypatch.setattr(store, "append", append)
    runner = Runner(
        store,
        config,
        FakeGame(),
        WorkingScript(note("x", "y")),
        episode_spending(store, config),
    )
    result = runner.run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["committed_actions"] == 0
    assert runner.notebook.entries == {}
    assert fold_notebook(store.events(result["episode_id"])).entries == {}


def test_helper_note_stays_durable_when_acknowledgment_write_fails(store, config, monkeypatch):
    original = store.append

    def append(eid, kind, payload, **kwargs):
        if kind == "helper_result":
            raise OSError("simulated failure after durable note write")
        return original(eid, kind, payload, **kwargs)

    monkeypatch.setattr(store, "append", append)
    result = Runner(
        store,
        config,
        FakeGame(),
        WorkingScript(note("x", "y")),
        episode_spending(store, config),
    ).run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    events = store.events(result["episode_id"])
    assert fold_notebook(events).entries == {"x": "y"}
    assert read_checkpoint(store, result["episode_id"], 0)["run_notebook"]["entries"] == {}


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
    policy = WorkingScript(annotated(select), annotated(play, text="I expect this hand to win"))
    Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    first, last = [
        context_payload(ctx, [], provider)
        for ctx in (policy.contexts[0], policy.contexts[-1])
    ]
    assert first["tools"] == last["tools"]
    assert (first["input"][0] == last["input"][0]) if provider == "openai" else (first["system"] == last["system"])
    messages = last.get("input", last.get("messages"))
    view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
    assert len(view["working_memory"]["frames"]) == 2
    assert view["run_notebook"]["entries"] == {"plan": "I expect this hand to win"}
    assert view["previous_action_outcome"] == policy.contexts[-1]["previous_action_outcome"]
    assert view["previous_action_outcome"]["action_type"] == "play_hand"
    assert view["previous_action_outcome"]["recorded_note_update"]["text"] == "I expect this hand to win"
    for definition in last["tools"]:
        if definition["name"] in ACTION_MODELS:
            schema = definition.get("parameters", definition.get("input_schema"))
            assert "note_update" in schema["required"] and "memory_update" not in schema["properties"]
            if definition["name"] in ("buy", "use_consumable", "choose_pack"):
                guidance = schema["properties"]["target_ids"]["description"]
                assert "list order does not move them" in guidance
                assert "Death converts the left selected card into the right selected card" in guidance
    args = {"observation_id": 0, "note_update": {"key": "k", "text": "v"}}
    assert decode_tool("cash_out", args)["note_update"] == args["note_update"]
    with pytest.raises(ValueError, match="LEGACY_MEMORY_UPDATE_NOT_ALLOWED"):
        decode_tool("cash_out", {"observation_id": 0, "memory_update": "retired"})


def test_unfrozen_context_matches_frozen_guidance_and_uses_bundle_outcome_flag(store, config):
    policy = WorkingScript(select)
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    checkpoint = read_checkpoint(store, result["episode_id"], 0)
    canonical = Observation.model_validate(checkpoint["observation"])
    ctx, _ = decision_context(canonical, [])
    frozen_definitions = {definition["name"]: definition for definition in policy.contexts[0]["tools"]}
    for definition in ctx["tools"]:
        if definition["name"] in ("buy", "use_consumable", "choose_pack"):
            assert definition == frozen_definitions[definition["name"]]
    frozen = json.loads(
        (store.episode_path(result["episode_id"], True) / "agent-protocol.json").read_text()
    )
    assert frozen["memory_policy"]["action_outcome"] == "previous-action-outcome-v2"
    without_feature = deepcopy(frozen)
    without_feature["memory_policy"].pop("action_outcome")
    old_context, _ = decision_context(canonical, [], frozen=without_feature)
    assert "previous_action_outcome" not in old_context


@pytest.mark.parametrize("name,selection", [
    ("buy", {"offer_id": "offer", "mode": "buy_and_use"}),
    ("use_consumable", {"consumable_id": "death"}),
    ("choose_pack", {"offer_id": "offer"}),
])
def test_target_guidance_never_reorders_a_submitted_selection(name, selection):
    args = {"observation_id": 4, "target_ids": ["right_card", "left_card"],
            "note_update": None, "decision_note": None, **selection}
    action = decode_tool(name, args)["envelope"]["action"]
    assert action["target_ids"] == ["right_card", "left_card"]
    assert args["target_ids"] == action["target_ids"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_parity_dynamic_notes_and_bounded_helper_feedback(store, monkeypatch, provider):
    config = config_for(provider)
    config.skills = "none"
    config.budgets.max_helper_calls_per_decision = 1
    provider_model = model(provider)
    config.models["luna"] = provider_model
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        user = next(
            message for message in body.get("input", body.get("messages", []))
            if message.get("role") == "user"
        )
        view = json.loads(user["content"])
        index = len(requests)
        if index == 1:
            name, args = "set_run_note", {"key": "plan", "text": "keep visible"}
        elif index == 2:
            name, args = "calculate", {"expression": "1+1"}
        elif index == 3:
            name, args = "select_blind", {
                "observation_id": 0,
                "decision_note": None,
                "blind_id": view["observation"]["state"]["revealed_blinds"][0]["id"],
                "note_update": {"key": "plan", "text": "updated with action"},
            }
        else:
            name, args = "abort_run", {"reason": "test finished"}
        usage = {
            "input_tokens": 10, "output_tokens": 1,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        }
        response = (
            {"status": "completed", "output": [{
                "type": "function_call", "call_id": f"call{index}",
                "name": name, "arguments": json.dumps(args),
            }], "usage": usage}
            if provider == "openai" else
            {"stop_reason": "tool_use", "content": [{
                "type": "tool_use", "id": f"call{index}", "name": name, "input": args,
            }], "usage": usage}
        )
        return httpx.Response(200, json=response)

    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "test-only"
    )
    policy = DirectProvider(
        provider_model, config.budgets,
        httpx.Client(transport=httpx.MockTransport(with_input_count(respond))),
    )
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    assert result["committed_actions"] == 1 and result["reason"] == "AGENT_ABORT"
    assert len(requests) == 4
    assert all(body["tools"] == requests[0]["tools"] for body in requests)

    def stable_prefix(body):
        return body["input"][0] if provider == "openai" else body["system"]

    assert all(stable_prefix(body) == stable_prefix(requests[0]) for body in requests)
    for index, body in enumerate(requests[1:], start=1):
        user = next(
            message for message in body.get("input", body.get("messages", []))
            if message.get("role") == "user"
        )
        view = json.loads(user["content"])
        expected = "updated with action" if index == 3 else "keep visible"
        assert view["run_notebook"]["entries"] == {"plan": expected}
        if index == 3:
            assert view["working_memory"]["frames"][0]["action"]["type"] == "select_blind"
    second = requests[1].get("input", requests[1].get("messages"))
    view = json.loads(next(message for message in second if message.get("role") == "user")["content"])
    assert "calculate" not in view["permitted_tools"]
    assert "Helper allowance exhausted" in json.dumps(requests[2])


def test_context_budget_prunes_history_before_live_helpers_and_notebook(store, config):
    result = Runner(
        store,
        config,
        FakeGame(),
        WorkingScript(select),
        episode_spending(store, config),
    ).run()
    observation = Observation.model_validate(read_checkpoint(store, result["episode_id"], 1)["observation"])
    memory = memory_fixture().view()
    ctx, exchanges = decision_context(observation, [], working_memory=memory)
    assert ctx["notebook_maintenance"]["oldest_decision_leaves_after_action"]["decision_id"] == 2
    bound = context_bound(ctx, exchanges)
    smaller, _ = decision_context(
        observation, [], working_memory=memory, byte_limit=bound - 500
    )
    assert len(smaller["working_memory"]["frames"]) < 3
    assert smaller["previous_action_outcome"] == ctx["previous_action_outcome"]
    assert smaller["working_memory"]["request_pruned_decisions"] > 0
    assert smaller["context_bytes_upper_bound"] <= bound - 500
    assert memory["frames"][0]["decision_id"] == 2  # Source never mutated.
    helpers = [{"operation": {"kind": "arithmetic", "expression": "1+1"}, "result": {"result": "2"}}] * 3
    ctx, _ = decision_context(observation, helpers, working_memory=memory)
    assert ctx["notebook_maintenance"]["next_helper_may_clear_older_results"]


def test_branch_restores_exact_predecision_context_and_rejects_tampering(store, config):
    root = Runner(store, config, FakeGame(), WorkingScript(
        {"kind": "arithmetic", "expression": "7*6"}, annotated(select),
        {"kind": "rules", "key": "FUTURE_PARENT"},
        note("plan", "LATER_PARENT"), play), episode_spending(store, config)).run()["episode_id"]
    assert verify_checkpoint(store, config, root, 1)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, root, 1, "agent_continue")
    child_policy = WorkingScript(play, note("later", "FUTURE_CHILD"))
    result = Runner(
        store,
        config,
        FakeGame(),
        child_policy,
        episode_spending(store, config),
    ).run(eid=child, resume=checkpoint, history_prefix=prefix)
    assert result["reason"] == "AGENT_ABORT"
    view = child_policy.contexts[0]["working_memory"]
    assert view == checkpoint["working_memory"]
    assert child_policy.contexts[0]["run_notebook"]["entries"] == {
        "plan": "Keep this conclusion"
    }
    assert view["frames"][0]["episode_id"] == root
    assert child_policy.contexts[0]["previous_action_outcome"]["recorded_note_update"] == (
        view["frames"][0]["recorded_note_update"]
    )
    assert view["frames"][0]["recorded_note_update"]["text"] == "Keep this conclusion"
    assert "FUTURE_PARENT" not in json.dumps(view)
    assert "LATER_PARENT" not in json.dumps(dict(child_policy.contexts[0]))
    assert verify_checkpoint(store, config, child, 1)["status"] == "passed"
    grandchild, snapshot, ancestors = prepare_branch(store, config, child, 1, "agent_continue")
    grand = WorkingScript({"kind": "action_result", "decision_id": 0})
    Runner(
        store,
        config,
        FakeGame(),
        grand,
        episode_spending(store, config),
    ).run(eid=grandchild, resume=snapshot, history_prefix=ancestors)
    assert grand.contexts[0]["working_memory"] == view
    assert grand.contexts[0]["run_notebook"]["entries"] == {
        "plan": "Keep this conclusion"
    }
    assert grand.contexts[0]["run_notebook"]["revision"] == snapshot["run_notebook"]["revision"]
    assert "FUTURE_CHILD" not in json.dumps(dict(grand.contexts[0]))
    assert grand.exchanges[1][-1]["result"]["references"]["action"]["episode_id"] == root
    corrupt = deepcopy(snapshot["working_memory"])
    corrupt["frames"][0]["helpers"][0]["result"] = {"result": "999"}
    with pytest.raises(ValueError, match="WORKING_MEMORY_SNAPSHOT_MISMATCH"):
        restore_working_memory(corrupt, ancestors, snapshot["observation"])
    tampered_notebook = deepcopy(snapshot["run_notebook"])
    tampered_notebook["entries"]["plan"] = "tampered"
    with pytest.raises(ValueError, match="RUN_NOTEBOOK_SNAPSHOT_MISMATCH"):
        restore_notebook(tampered_notebook, ancestors)


def read_action_result(events, observation, **kwargs):
    chunks = []
    offset = 0
    while True:
        result = helper(
            ActionResult(kind="action_result", byte_offset=offset, **kwargs),
            events,
            {},
            observation,
        )
        if "error" in result:
            return result
        chunks.append(result["content"])
        if result["complete"]:
            return {"value": json.loads("".join(chunks)), "references": result["references"]}
        offset = result["next_offset"]


def test_action_result_uses_exact_public_cutoff(store, config):
    policy = WorkingScript(select, play, note("later", "do not include in old receipt"))
    result = Runner(store, config, FakeGame(), policy, episode_spending(store, config)).run()
    events = store.events(result["episode_id"])
    observations = [
        Observation.model_validate(event["payload"])
        for event in events
        if event["type"] == "observation"
    ]
    receipt = read_action_result(events, observations[2])
    assert receipt["value"]["action"]["type"] == "play_hand"
    assert receipt["value"]["recorded_decision_note"].startswith("Recorded note")
    assert receipt["value"]["hand_score"] is None
    assert receipt["value"]["scoring_breakdown"] is None
    assert receipt["value"]["observed_result"] == observations[2].last_action.model_dump(
        mode="json"
    )
    before = read_action_result(events, observations[2], decision_id=1, section="before")
    assert before["value"]["state"] == observations[1].state.model_dump(mode="json")
    earlier = read_action_result(events, observations[1])
    assert earlier["value"]["action"]["type"] == "select_blind"
    for decision_id in (1, 99):
        assert read_action_result(events, observations[1], decision_id=decision_id)["error"] == (
            "ACTION_RESULT_NOT_AVAILABLE"
        )
    assert "do not include" not in json.dumps(earlier)


def test_prospective_review_and_export_keep_current_edit_behind_action_reveal(store, config):
    observation = project(FakeGame().observe_private())
    book = RunNotebook()
    change, _ = book.propose("set_run_note", "k", "x" * 4095)
    book.apply(change)
    ctx, exchanges = decision_context(
        observation, [], notebook=book.view(), helper_remaining=24
    )
    body = context_payload(ctx, exchanges, "openai")
    assert "x" * 4095 not in body["input"][0]["content"][0]["text"]
    assert "x" * 4095 in body["input"][1]["content"]
    assert ctx["context_bytes_upper_bound"] >= len(body["input"][1]["content"].encode())
    with pytest.raises(ValueError, match="LOCAL_CONTEXT_LIMIT"):
        decision_context(observation, [], notebook=book.view(), byte_limit=2000)

    eid = Runner(
        store,
        config,
        FakeGame(),
        WorkingScript(annotated(select, text="CURRENT_EDIT_SENTINEL"), play),
        episode_spending(store, config),
    ).run()["episode_id"]
    review = ReviewService(store)
    token = review.open(eid)["review_token"]
    assert "CURRENT_EDIT_SENTINEL" not in json.dumps(review.view(token))
    review.advance(token)
    assert "CURRENT_EDIT_SENTINEL" in json.dumps(review.view(token)["action_events"])
    exported = episode_export(store, eid)
    assert any(e["type"] == "run_note" for e in exported["events"])
    assert "working_memory" in json.dumps(exported)
