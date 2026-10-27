"""Layered requests: stable layers first, cache breakpoints placed by each provider's rules.

L0 is the frozen prompt, rules kernel and tools; L1 the run configuration; L2 the sealed
decision message; L3 the follow-up exchanges. All providers here are synthetic.
"""

import json
from copy import deepcopy

import pytest
from test_boundary import project
from test_claude_support import claude
from test_decision_transcript import delivery, response, unmarked
from test_provider_continuations import model
from test_provider_reporting_protocol import frozen_run

from balatro_horizons.config import Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.context.conversation import (
    CONTEXT_POLICY,
    WIRE_POLICIES,
    DecisionConversation,
)
from balatro_horizons.harness.context.freeze import freeze_protocol, restore_protocol
from balatro_horizons.harness.skills import load_guide
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.harness.transport.conversation import encode
from balatro_horizons.review.dev_trace import delivered_results
from balatro_horizons.review.protocol_label import protocol_metadata
from balatro_horizons.storage.journal import digest

MARKER = {"openai": "prompt_cache_breakpoint", "anthropic": "cache_control"}
VALUE = {"openai": {"mode": "explicit"}, "anthropic": {"type": "ephemeral", "ttl": "5m"}}
LIMIT = 4  # OpenAI: four cache writes per request; Anthropic: four cache_control blocks


def provider_model(provider):
    return model("openai") if provider == "openai" else claude("Haiku 5.5")


def marked(value, key, path=()):
    """Paths of every block carrying a cache marker."""
    found = []
    if isinstance(value, dict):
        if key in value:
            found.append(path)
        for name, item in value.items():
            found += marked(item, key, path + (name,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += marked(item, key, path + (index,))
    return found


def decision_bodies(provider, helpers):
    ctx, _ = decision_context(project(FakeGame().observe_private()), [], helper_remaining=8)
    conversation, exchanges, bodies, updates = DecisionConversation(ctx), [], [], []
    policy = DirectProvider(provider_model(provider), Limits())
    try:
        for index in range(helpers + 1):
            current, delivered = delivery(conversation, exchanges, attempts=index)
            bodies.append(policy.request(current, delivered))
            updates.append(delivered[-1]["model_result"]["context_update"] if delivered else None)
            if index == helpers:
                break
            raw = policy.parse(response(provider, index))
            exchanges.append({"operation": raw, "result": {"result": f"answer {index}"},
                              "tool_call": deepcopy(policy.last_tool_call),
                              "provider_turn": deepcopy(policy.last_provider_turn)})
    finally:
        policy.client.close()
    return bodies, updates


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_first_request_has_only_the_prefix_breakpoint_and_follow_ups_add_a_rolling_one(provider):
    bodies, _ = decision_bodies(provider, helpers=8)
    key, field = MARKER[provider], "input" if provider == "openai" else "messages"
    prefix = ("input", 0, "content", 0) if provider == "openai" else ("system", 0)
    assert marked(bodies[0], key) == [prefix]
    for index, body in enumerate(bodies[1:], start=1):
        paths = marked(body, key)
        assert len(paths) <= LIMIT
        assert all(_at(body, path)[key] == VALUE[provider] for path in paths)
        last = len(body[field]) - 1
        ends = {path[:2] for path in paths if path != prefix}
        if provider == "openai":
            # A lookup reads only boundaries this request marks: keep the previous end.
            expected = {(field, last)} | ({(field, len(bodies[index - 1][field]) - 1)} if index > 1 else set())
            assert ends == expected and prefix in paths
            assert _at(body, (field, last))["type"] == "function_call_output"
        else:
            # One rolling block on the latest user turn; lookback reaches the previous write.
            assert paths == [prefix, (field, last, "content", len(body[field][last]["content"]) - 1)]
            assert body[field][last]["role"] == "user"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_decision_message_and_transcript_stay_stable_and_counters_still_arrive(provider):
    bodies, updates = decision_bodies(provider, helpers=8)
    field = "input" if provider == "openai" else "messages"
    message = 1 if provider == "openai" else 0
    for previous, body in zip(bodies, bodies[1:], strict=False):
        assert body[field][message] == bodies[0][field][message]
        assert unmarked(body[field])[:len(previous[field])] == unmarked(previous[field])
    for index, update in enumerate(updates[1:], start=1):
        assert update["helper_status"] == {"remaining": 8 - index}
        assert update["remaining_budget"]["helper_calls_remaining"] == 8 - index


def _at(value, path):
    for key in path:
        value = value[key]
    return value


def _stated_line(prefix, dotted):
    lines = [line for line in prefix.split("\n") if line.startswith(f"- {dotted}: ")]
    assert len(lines) == 1, dotted
    return lines[0]


STATED = [
    ("observation", "objective"), ("observation", "public_contract_version"),
    ("observation", "details_available"), ("observation", "presentation"),
    ("observation", "last_action", "version"), ("current_costs", "version"), ("current_costs", "note"),
    ("run_notebook", "version"), ("helper_status", "message"), ("working_memory", "version"),
    ("working_memory", "max_decisions"), ("working_memory", "max_bytes"),
    ("working_memory", "helpers_per_decision"), ("working_memory", "max_helper_bytes"),
    ("working_memory", "meaning"), ("notebook_maintenance", "message"),
    ("previous_action_outcome", "version"),
]


@pytest.mark.parametrize("phase", ["SELECTING_HAND", "BLIND_SELECT"])
@pytest.mark.parametrize("helper_remaining", [8, 0])
def test_static_fields_leave_the_decision_message_for_the_frozen_prefix(phase, helper_remaining):
    game = FakeGame()
    game.phase = phase
    ctx, _ = decision_context(project(game.observe_private()), [], skills=load_guide()[1],
                              helper_remaining=helper_remaining)
    ctx.observation["last_action"] = {"version": "public_delta_v1", "action_type": "play_hand"}
    ctx.previous_action_outcome = {"version": "previous-action-outcome-v2", "action_type": "play_hand"}
    source = {name: ctx[name] for name in ("observation", "current_costs", "run_notebook", "helper_status",
                                           "working_memory", "notebook_maintenance", "previous_action_outcome")}
    policy = DirectProvider(model("openai"), Limits())
    try:
        body = policy.request(ctx, [])
    finally:
        policy.client.close()
    prefix = body["input"][0]["content"][0]["text"]
    message = json.loads(body["input"][1]["content"])
    for path in STATED:
        container = _at(message, path[:-1])
        assert path[-1] not in container, path
        assert encode(_at(source, path)) in _stated_line(prefix, ".".join(path))
    assert "retrieval_context" not in message["observation"]
    assert message["helper_status"] == {"remaining": helper_remaining}
    assert message["observation"]["last_action"] == {"action_type": "play_hand"}


def test_frozen_rules_kernel_states_the_omitted_fields_once(config):
    bundle = freeze_protocol(config, Baseline("heuristic"), {})
    for path in STATED:
        dotted = ".".join(path)
        assert bundle["rules_kernel"].count(f"- {dotted}: ") == 1, dotted


def test_new_identity_replaces_the_append_only_one_without_migrating_old_runs(store, config, monkeypatch):
    assert CONTEXT_POLICY == "layered_decision_v1"
    assert WIRE_POLICIES == {"openai": "openai_responses_v2", "anthropic": "anthropic_messages_v2"}
    bundle = freeze_protocol(config, Baseline("heuristic"), {})
    assert bundle["context_policy"] == CONTEXT_POLICY
    old = {**deepcopy(bundle), "context_policy": "append_only_decision_v1"}
    frozen = deepcopy(old)
    monkeypatch.setattr("balatro_horizons.harness.context.freeze.read_protocol", lambda *_args: deepcopy(old))
    checkpoint = {"agent_protocol": {"episode_id": "a" * 32, "hash": digest(old)}}
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_CONTEXT_POLICY_CHANGED$"):
        restore_protocol(store, checkpoint)
    assert old == frozen


@pytest.mark.parametrize("wire", ["openai_responses_v1", "anthropic_messages_v1"])
def test_old_identities_replay_unchanged_in_review(store, wire):
    eid, _, _ = frozen_run(store, {"context_policy": "append_only_decision_v1", "provider_wire_policy": wire})
    assert protocol_metadata(store, eid)["context_policy"] == "append_only_decision_v1"
    assert protocol_metadata(store, eid)["provider_wire_policy"] == wire
    old = {"input": [{"type": "function_call_output", "call_id": "c1", "output": '{"result":"old"}'}]}
    new = {"input": [{"type": "function_call_output", "call_id": "c2", "output": [
        {"type": "input_text", "text": '{"result":"new"}', "prompt_cache_breakpoint": {"mode": "explicit"}}]}]}
    assert list(delivered_results(old)) == [("c1", '{"result":"old"}')]
    assert list(delivered_results(new)) == [("c2", '{"result":"new"}')]
