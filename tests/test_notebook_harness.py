"""Notebook/retrieval integration uses synthetic mechanics and mocked providers only."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project
from test_provider_continuations import model
from test_tool_interface import config_for

from balatro_horizons.agents.notebook import RunNotebook, fold_notebook, restore_notebook
from balatro_horizons.agents.protocol import ActionResult, decision_context, helper
from balatro_horizons.agents.providers import DirectProvider, context_payload
from balatro_horizons.agents.tool_interface import decode_tool
from balatro_horizons.engine.certification import read_checkpoint, verify_checkpoint
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.review.branches import prepare_branch
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner


def select(ctx):
    obs = ctx["observation"]
    return {"kind": "action", "envelope": {"observation_id": obs["observation_id"],
            "action": {"type": "select_blind", "blind_id": obs["state"]["revealed_blinds"][0]["id"]}}}


def play(ctx):
    obs = ctx["observation"]
    return {"kind": "action", "envelope": {"observation_id": obs["observation_id"],
            "decision_note": "Recorded note; not necessarily a prediction.",
            "action": {"type": "play_hand", "card_ids": [c["id"] for c in obs["state"]["hand"][:3]]}}}


def note(key, text):
    return {"kind": "set_run_note", "key": key, "text": text}


class Script:
    paid = False
    name = "heuristic"
    interface = "tools_v6"

    def __init__(self, *operations):
        self.operations = iter(operations)
        self.contexts = []
        self.exchanges = []

    def decide(self, ctx, exchanges):
        self.contexts.append(deepcopy(ctx))
        self.exchanges.append(deepcopy(exchanges))
        operation = next(self.operations, {"kind": "abort", "reason": "test finished"})
        return operation(ctx) if callable(operation) else operation


def test_note_accounting_unicode_atomic_updates_and_delete():
    book = RunNotebook(limit=10)
    change, _ = book.propose("set_run_note", "é", "猫" * 9)
    book.apply(change)
    original = book.snapshot()
    change, result = book.propose("set_run_note", "é", "猫" * 10)
    assert change is None and result["error"] == "RUN_NOTEBOOK_LIMIT"
    assert book.snapshot() == original
    change, _ = book.propose("set_run_note", "é", "short")
    book.apply(change)
    change, _ = book.propose("set_run_note", "x", "ok")
    book.apply(change)
    change, _ = book.propose("delete_run_note", "é")
    book.apply(change)
    assert book.entries == {"x": "ok"}
    assert book.propose("delete_run_note", "missing")[1]["error"] == "RUN_NOTE_NOT_FOUND"
    for key in ("", " ", "x\n", "a" * 65):
        assert book.propose("set_run_note", key, "text")[1]["error"] == "INVALID_RUN_NOTE_KEY"


def test_write_visible_immediately_survives_pruning_and_game_actions(store, config):
    policy = Script(note("scoring/pair", "one observed result"),
                    *[{"kind": "arithmetic", "expression": "1+1"}] * 4,
                    select, play)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 2 and result["reason"] == "AGENT_ABORT"
    assert policy.contexts[0]["run_notebook"]["revision"] == 0
    for ctx in policy.contexts[1:]:
        assert ctx["run_notebook"]["entries"] == {"scoring/pair": "one observed result"}
        assert "memory" not in ctx["observation"]
    assert policy.contexts[5]["observation"]["retrieval_context"]["cleared"][0]["reload"] == {
        "source": "run_notebook", "mutation_already_recorded": True}
    events = store.events(result["episode_id"])
    observations = [e for e in events if e["type"] == "observation"]
    assert observations[0]["payload"]["memory"] == ""
    assert observations[1]["payload"]["last_action"]["action_type"] == "select_blind"
    assert observations[2]["payload"]["last_action"]["action_type"] == "play_hand"
    assert fold_notebook(events).snapshot() == read_checkpoint(store, result["episode_id"], 2)["run_notebook"]


def test_oversized_write_consumes_helper_and_limit_feedback_allows_game_action(store, config):
    config.budgets.memory_max_characters = 8
    config.budgets.max_helper_calls_per_decision = 1
    policy = Script(note("key", "too much text"), note("k", "v"), select)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 1 and result["reason"] == "AGENT_ABORT"
    assert policy.exchanges[1][-1]["result"]["error"] == "RUN_NOTEBOOK_LIMIT"
    assert policy.exchanges[2][-1]["result"]["error"] == "HELPER_LIMIT_REACHED"
    assert "set_run_note" not in policy.contexts[1]["allowed_tools"]
    assert policy.contexts[1]["run_notebook"]["entries"] == {}
    assert not any(e["type"] == "run_note" for e in store.events(result["episode_id"]))


def test_repeated_exhausted_helpers_terminate_without_substituted_action(store, config):
    config.budgets.max_helper_calls_per_decision = 0
    policy = Script(*[{"kind": "arithmetic", "expression": "1+1"}] * 3)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["reason"] == "AGENT_PROTOCOL_FAILURE" and result["committed_actions"] == 0
    assert len(policy.contexts) == 3


def test_failed_journal_append_does_not_make_note_visible(store, config, monkeypatch):
    original = store.append

    def append(eid, kind, payload, **kwargs):
        if kind == "run_note":
            raise OSError("simulated storage failure")
        return original(eid, kind, payload, **kwargs)

    monkeypatch.setattr(store, "append", append)
    runner = Runner(store, config, FakeGame(), Script(note("x", "y")))
    result = runner.run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert runner.notebook.entries == {} and result["committed_actions"] == 0


def test_note_is_durable_when_acknowledgment_fails(store, config, monkeypatch):
    original = store.append

    def append(eid, kind, payload, **kwargs):
        if kind == "helper_result":
            raise OSError("simulated failure after durable note write")
        return original(eid, kind, payload, **kwargs)

    monkeypatch.setattr(store, "append", append)
    result = Runner(store, config, FakeGame(), Script(note("x", "y"))).run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    events = store.events(result["episode_id"])
    assert fold_notebook(events).entries == {"x": "y"}
    assert read_checkpoint(store, result["episode_id"], 0)["run_notebook"]["entries"] == {}


def test_notebook_replay_rejects_missing_revisions_or_malformed_mutations():
    book = RunNotebook()
    change, _ = book.propose("set_run_note", "x", "y")
    for corrupted in (None, {**change, "revision": 2}, {**change, "revision": True},
                      {**change, "text": "different"}, {**change, "extra": "unrecognized"}):
        with pytest.raises(ValueError, match="RUN_NOTEBOOK_JOURNAL_MISMATCH"):
            book.apply(corrupted)
        assert book.entries == {} and book.revision == 0
    book.apply(change)
    with pytest.raises(ValueError, match="RUN_NOTEBOOK_JOURNAL_MISMATCH"):
        book.apply(change)


def test_v6_rejects_memory_replacement_but_legacy_retains_it(store, config):
    with pytest.raises(ValueError, match="LEGACY_MEMORY_UPDATE_NOT_ALLOWED"):
        decode_tool("cash_out", {"observation_id": 0, "memory_update": None}, interface="tools_v6")
    operation = decode_tool("cash_out", {"observation_id": 0, "memory_update": "legacy"}, interface="tools_v5")
    assert operation["envelope"]["memory_update"] == "legacy"
    policy = Script(lambda ctx: {"kind": "action", "envelope": {
        **select(ctx)["envelope"], "memory_update": "no competing store"}}, select)
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 1
    assert policy.exchanges[1][-1]["result"]["error"] == "LEGACY_MEMORY_UPDATE_NOT_ALLOWED"


def read_result(events, observation, **kwargs):
    chunks, offset = [], 0
    while True:
        result = helper(ActionResult(kind="action_result", byte_offset=offset, **kwargs),
                        events, {}, observation, interface="tools_v6")
        if "error" in result:
            return result
        chunks.append(result["content"])
        if result["complete"]:
            return {"value": json.loads("".join(chunks)), "references": result["references"]}
        offset = result["next_offset"]


def test_receipt_and_conditions_use_exact_public_cutoff(store, config):
    policy = Script(select, play, note("later", "do not include in old receipt"))
    result = Runner(store, config, FakeGame(), policy).run()
    events = store.events(result["episode_id"])
    from balatro_horizons.contracts import Observation

    observations = [Observation.model_validate(e["payload"]) for e in events if e["type"] == "observation"]
    receipt = read_result(events, observations[2])
    assert receipt["value"]["action"]["type"] == "play_hand"
    assert receipt["value"]["recorded_decision_note"].startswith("Recorded note")
    assert receipt["value"]["hand_score"] is None
    assert receipt["value"]["scoring_breakdown"] is None
    assert receipt["value"]["observed_result"] == observations[2].last_action.model_dump(mode="json")
    before = read_result(events, observations[2], decision_id=1, section="before")
    assert before["value"]["state"] == observations[1].state.model_dump(mode="json")
    earlier = read_result(events, observations[1])
    assert earlier["value"]["action"]["type"] == "select_blind"
    assert read_result(events, observations[1], decision_id=1)["error"] == "ACTION_RESULT_NOT_AVAILABLE"
    assert read_result(events, observations[1], decision_id=99)["error"] == "ACTION_RESULT_NOT_AVAILABLE"
    assert "do not include" not in json.dumps(earlier)


def test_branch_inherits_only_boundary_notebook_and_ancestry(store, config):
    root = Runner(store, config, FakeGame(), Script(note("plan", "before boundary"), select,
                  note("plan", "LATER_PARENT"), play)).run()["episode_id"]
    assert verify_checkpoint(store, config, root, 1)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, root, 1, "agent_continue")
    policy = Script(note("child", "retained"), play, note("child", "FUTURE_CHILD"))
    result = Runner(store, config, FakeGame(), policy).run(eid=child, resume=checkpoint, history_prefix=prefix)
    assert result["reason"] == "AGENT_ABORT"
    assert policy.contexts[0]["run_notebook"]["entries"] == {"plan": "before boundary"}
    assert verify_checkpoint(store, config, child, 1)["status"] == "passed"
    grandchild, snapshot, ancestors = prepare_branch(store, config, child, 1, "agent_continue")
    grand = Script({"kind": "action_result", "decision_id": 0})
    Runner(store, config, FakeGame(), grand).run(eid=grandchild, resume=snapshot, history_prefix=ancestors)
    assert grand.contexts[0]["run_notebook"]["entries"] == {"plan": "before boundary"}
    assert "LATER_PARENT" not in json.dumps(ancestors)
    assert "FUTURE_CHILD" not in json.dumps(ancestors)
    assert grand.exchanges[1][-1]["result"]["references"]["action"]["episode_id"] == root
    tampered = deepcopy(snapshot["run_notebook"])
    tampered["entries"]["plan"] = "tampered"
    with pytest.raises(ValueError, match="RUN_NOTEBOOK_SNAPSHOT_MISMATCH"):
        restore_notebook(tampered, ancestors)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_parity_dynamic_notes_and_bounded_helper_feedback(store, monkeypatch, provider):
    config = config_for(provider)
    config.skills = "none"
    config.budgets.max_helper_calls_per_decision = 1
    cfg = model(provider)
    cfg.settings["harness_interface"] = "tools_v6"
    config.models["luna"] = cfg
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        user = next(m for m in body.get("input", body.get("messages", [])) if m.get("role") == "user")
        view = json.loads(user["content"])
        index = len(requests)
        if index == 1:
            name, args = "set_run_note", {"key": "plan", "text": "keep visible"}
        elif index == 2:
            name, args = "calculate", {"expression": "1+1"}
        elif index == 3:
            name, args = "select_blind", {"observation_id": 0, "decision_note": None,
                "blind_id": view["observation"]["state"]["revealed_blinds"][0]["id"]}
        else:
            name, args = "abort_run", {"reason": "test finished"}
        usage = {"input_tokens": 10, "output_tokens": 1,
                 "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
        response = {"status": "completed", "output": [{"type": "function_call", "call_id": f"call{index}",
            "name": name, "arguments": json.dumps(args)}], "usage": usage} if provider == "openai" else {
            "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": f"call{index}",
            "name": name, "input": args}], "usage": usage}
        return httpx.Response(200, json=response)

    monkeypatch.setenv("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "test-only")
    policy = DirectProvider(cfg, config.budgets, httpx.Client(transport=httpx.MockTransport(with_input_count(respond))))
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["committed_actions"] == 1 and result["reason"] == "AGENT_ABORT"
    assert len(requests) == 4
    assert all(r["tools"] == requests[0]["tools"] for r in requests)
    def stable_prefix(body):
        return body["input"][0] if provider == "openai" else body["system"]
    assert all(stable_prefix(r) == stable_prefix(requests[0]) for r in requests)
    for body in requests[1:]:
        first = next(m for m in body.get("input", body.get("messages", [])) if m.get("role") == "user")
        assert json.loads(first["content"])["run_notebook"]["entries"] == {"plan": "keep visible"}
    second = requests[1].get("input", requests[1].get("messages"))
    view = json.loads(next(m for m in second if m.get("role") == "user")["content"])
    assert "calculate" not in view["permitted_tools"]
    assert "Helper allowance exhausted" in json.dumps(requests[2])


def test_notebook_is_in_dynamic_context_bound_and_export_review(store, config):
    observation = project(FakeGame().observe_private())
    book = RunNotebook()
    change, _ = book.propose("set_run_note", "k", "x" * 4095)
    book.apply(change)
    ctx, exchanges = decision_context(observation, [], interface="tools_v6", notebook=book.view(), helper_remaining=24)
    body = context_payload(ctx, exchanges, "openai", "tools_v6")
    assert "x" * 4095 not in body["input"][0]["content"][0]["text"]
    assert "x" * 4095 in body["input"][1]["content"]
    assert ctx["context_bytes_upper_bound"] >= len(body["input"][1]["content"].encode())
    with pytest.raises(ValueError, match="LOCAL_CONTEXT_LIMIT"):
        decision_context(observation, [], interface="tools_v6", notebook=book.view(), byte_limit=2000)
    result = Runner(store, config, FakeGame(), Script(note("n", "<script>not executable</script>"))).run()
    export = episode_export(store, result["episode_id"])
    assert any(e["type"] == "run_note" for e in export["events"])
    review = ReviewService(store)
    token = review.open(result["episode_id"])["review_token"]
    assert "<script>" not in json.dumps(review.view(token))
    review.advance(token)
    assert any(e["type"] == "run_note" for e in review.view(token)["action_events"])
