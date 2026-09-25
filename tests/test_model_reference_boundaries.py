"""Regression probes for every v8 delivery boundary, without live providers."""

import json
import re
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from pydantic import ValidationError
from runner_support import episode_spending
from test_boundary import project
from test_harness_tools import config_for
from test_model_reference_delivery import response
from test_provider_continuations import exchange, model

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.config import EVENT_SUMMARY_CHARACTERS, Limits
from balatro_horizons.contracts import ActionEnvelope, RecentPublicEvent
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.contract import Operation
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.transport import DirectProvider, context_payload
from balatro_horizons.workbench.policies import HumanSequencePolicy


def delivered_view(body, provider):
    messages = body["input" if provider == "openai" else "messages"]
    assert not re.search(r"h_[0-9a-f]{20}", json.dumps(messages))
    return json.loads(next(m for m in messages if m.get("role") == "user")["content"])


def eight_card_observation():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    hand = raw["visible"]["hand"]
    hand.extend({**deepcopy(hand[0]), "native_id": f"extra-{index}"} for index in range(5))
    raw["visible"]["available_action_types"].append("reorder")
    return project(raw)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_eight_card_reorder_summary_projects_before_truncation(provider):
    obs = eight_card_observation()
    action = ActionEnvelope.model_validate({"observation_id": obs.observation_id,
        "action": {"type": "reorder", "area": "hand", "ordered_ids": [c.id for c in obs.state.hand][::-1]}})
    validate_action(action, obs)
    summary = json.dumps(action.action.model_dump(mode="json"))
    assert len(summary) > EVENT_SUMMARY_CHARACTERS
    obs.recent_public_events = [RecentPublicEvent(event_id="reorder", event_type="action_commit", summary=summary)]
    ctx = context(obs)
    view = delivered_view(context_payload(ctx, [], provider), provider)
    event = view["observation"]["recent_public_events"][0]
    assert not event.get("truncated", False)
    assert json.loads(event["summary"]) == {"type": "reorder", "area": "hand",
        "ordered_ids": [card["id"] for card in view["observation"]["state"]["hand"]][::-1]}
    assert all(type(value) is int for value in json.loads(event["summary"])["ordered_ids"])
    assert "h_" not in event["summary"]
    assert obs.recent_public_events[0].summary == summary


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_large_summary_is_bounded_only_in_default_delivery_not_inspection(provider):
    obs = eight_card_observation()
    summary = json.dumps({"card_ids": [c.id for c in obs.state.hand], "detail": "猫" * 500}, ensure_ascii=False)
    obs.recent_public_events = [RecentPublicEvent(event_id="large", event_type="action_commit", summary=summary)]
    ctx = context(obs)
    event = delivered_view(context_payload(ctx, [], provider), provider)["observation"]["recent_public_events"][0]
    full = ctx.model_references.project({"summary": summary})["summary"]
    assert event["truncated"] and event["summary"] == full[:EVENT_SUMMARY_CHARACTERS]
    assert "h_" not in event["summary"]
    page = helper(Operation.validate_python({"kind": "inspect_page", "section": "recent_public_events"}),
                  [], {}, obs, references=ctx.model_references)
    assert page["complete"] and json.loads(page["content"])[0]["summary"] == full
    assert obs.recent_public_events[0].summary == summary


class InspectingHuman(Baseline):
    actor = "human"

    def __init__(self, section):
        super().__init__("heuristic")
        self.section = section
        self.inspected = False

    def decide(self, ctx, exchanges):
        if not self.inspected:
            self.inspected = True
            return {"kind": "inspect_page", "section": self.section, "offset": 0}
        return super().decide(ctx, exchanges)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("section", ["hand", "revealed_blinds"])
def test_human_helper_receipt_stays_indexed_when_provider_resumes(store, monkeypatch, provider, section):
    config, requests = config_for(provider), []
    monkeypatch.setenv("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "test-mocked-only")

    def receive(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response(provider, "abort_run", {"reason": "handoff inspected"}))

    game = FakeGame()
    if section == "hand":
        game.phase = "SELECTING_HAND"
    with httpx.Client(transport=httpx.MockTransport(with_input_count(receive))) as client:
        continuation = DirectProvider(config.models["luna"], config.budgets, client=client)
        policy = HumanSequencePolicy(InspectingHuman(section), continuation, steps=1)
        result = Runner(store, config, game, policy, episode_spending(store, config)).run()
    assert result["reason"] == "AGENT_ABORT" and len(requests) == 1
    view = delivered_view(requests[0], provider)
    receipts = view["working_memory"]["frames"][0]["helpers"]
    assert len(receipts) == 1
    cards = json.loads(receipts[0]["result"]["content"])
    assert cards and all(type(card["id"]) is int for card in cards)
    events = store.events(result["episode_id"])
    committed = [event for event in events if event["type"] == "action_commit"]
    assert len(committed) == 1 and committed[0]["actor"] == "human"
    recorded = next(event["payload"]["result"] for event in events if event["type"] == "helper_result")
    assert recorded["content"] == receipts[0]["result"]["content"]
    snapshot = read_checkpoint(store, result["episode_id"], 1)
    assert snapshot["working_memory"]["frames"][0]["helpers"][0]["result"]["content"] == recorded["content"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("native_turn", [False, True])
def test_feedback_exchange_ids_are_projected_for_both_provider_formats(store, provider, native_turn):
    obs = eight_card_observation()
    obs.action_constraints["play_hand"]["required_ids"] = [obs.state.hand[0].id]
    ctx, config = context(obs), config_for(provider)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        policy = DirectProvider(model(provider), Limits(), client=client)
        policy.request(ctx, [])
        raw = policy.parse(response(provider, "play_hand", {"observation_id": obs.observation_id, "card_ids": []}))
        runner = Runner(store, config, FakeGame(), policy, episode_spending(store, config))
        error = InvalidAction("INVALID_CARD_SELECTION")
        feedback = runner._tool_feedback(error, error.code, obs)
        blind = project(FakeGame().observe_private()).state.revealed_blinds[0].id
        feedback["current_blind_id"] = blind
        canonical = deepcopy(feedback)
        item = exchange(policy, raw, feedback)
        if not native_turn:
            item.pop("provider_turn")
        body = policy.request(ctx, [item])
        delivered_view(body, provider)
    if provider == "openai":
        result = json.loads(next(m["output"] for m in body["input"] if m.get("type") == "function_call_output"))
    else:
        result = json.loads(body["messages"][-1]["content"][0]["content"])
    ids = [ctx.model_references.objects[card.id] for card in obs.state.hand]
    assert result["current_hand_ids"] == ids
    assert result["constraints"]["play_hand"]["required_ids"] == ids[:1]
    assert type(result["current_blind_id"]) is int
    assert result["current_blind_id"] == ctx.model_references.objects[blind]
    assert feedback == canonical


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("field,value", [("id", 5), ("card_ids", [True]), ("episode_id", 999)])
def test_undeclared_ids_get_schema_errors_not_reference_errors(provider, field, value):
    ctx = context(eight_card_observation())
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        policy = DirectProvider(model(provider), Limits(), client=client)
        policy.request(ctx, [])
        raw = policy.parse(response(provider, "inspect_state", {"section": "hand", "offset": 0, field: value}))
    with pytest.raises(ValidationError) as caught:
        Operation.validate_python(raw)
    assert Runner._invalid_code(caught.value) == "INVALID_OPERATION_SCHEMA"
    assert any(item["type"] == "extra_forbidden" and item["loc"][-1] == field for item in caught.value.errors())
