"""B1: native-shaped fixtures through the final provider request; no live APIs/game."""

import json
from copy import deepcopy

import httpx
import pytest
from test_boundary import project
from test_provider_continuations import model

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.config import Limits
from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.state import normalize
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.contract import Operation
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.observations.projection import HandleIssuer
from balatro_horizons.service import RunService
from balatro_horizons.workbench.service import WorkbenchService as ReviewService


def native_state(phase="SHOP"):
    return {
        "state": phase,
        "money": 20,
        "ante_num": 2,
        "round_num": 4,
        "round": {"hands_left": 4, "discards_left": 3, "chips": 0, "reroll_cost": 5},
        "bh": {
            "blind_on_deck": "Big",
            "target": 800,
            "pack_choices": 2,
            "owned_vouchers": [
                {"label": "Seed Money", "effects": ["Raise the cap on interest to $10"]}
            ],
            "tags": ["PRIVATE_ENGINE_TAG_KEY"],
            "pending_tags": [
                {"label": "Double Tag", "effects": ["Gives a copy of the next selected Tag"]}
            ],
            "rng": "PRIVATE_RNG",
            "deck_composition": {},
        },
        "blinds": {
            "small": {"name": "Small Blind", "score": 600, "status": "DEFEATED"},
            "big": {
                "name": "Big Blind",
                "score": 800,
                "status": "SELECT",
                "tag_name": "Investment Tag",
                "tag_effect": "After defeating the Boss Blind, gain $25",
            },
            "boss": {
                "name": "The Plant",
                "score": 1200,
                "status": "UPCOMING",
                "effect": "All face cards are debuffed",
            },
        },
        "jokers": {"cards": [], "limit": 5},
        "consumables": {"cards": [], "limit": 2},
        "shop": {"cards": [playing_card()]},
        "pack": {"cards": [playing_card()]},
    }


def playing_card():
    return {
        "id": 991,
        "label": "Bonus Card",
        "set": "ENHANCED",
        "state": {"hidden": False},
        "value": {"rank": "K", "suit": "H", "effect": "+30 chips"},
        "modifier": {"edition": "FOIL"},
        "cost": {"buy": 7},
    }


def request(observation, provider="openai"):
    settings = model(provider)
    ctx, exchanges = decision_context(observation, [])
    # Real request construction/serialization; no transport can make a network call.
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected API call"))
    ) as client:
        body = DirectProvider(settings, Limits(), client=client).request(ctx, exchanges)
    return json.loads(json.dumps(body))


def delivered_observation(body, provider):
    messages = body["input" if provider == "openai" else "messages"]
    message = next(m for m in messages if m.get("role") == "user")
    return json.loads(message["content"])["observation"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "phase,price,action_type", [("SHOP", "7", "buy"), ("STANDARD_PACK", "0", "choose_pack")]
)
def test_visible_playing_offer_identity_reaches_final_request(
    provider, phase, price, action_type
):
    raw = native_state(phase)
    original = deepcopy(raw)
    obs = project(normalize(raw))
    view = delivered_observation(request(obs, provider), provider)
    (offer,) = view["state"]["offers"]
    assert (offer["rank"], offer["suit"], offer["label"]) == ("K", "Hearts", "K of Hearts")
    assert offer["effects"] == ["+30 chips", "edition: FOIL"]
    assert offer["price"] == price and offer["acquire_allowed"] and not offer["buy_and_use_allowed"]
    envelope = ActionEnvelope.model_validate(
        {"observation_id": 0, "action": {"type": action_type, "offer_id": offer["id"]}}
    )
    validate_action(envelope, obs)
    assert raw == original  # Formatting does not edit the engine state/mechanics.
    assert view.get("public_contract_version", view.get("schema_version")) == "1.1"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("phase", ["SHOP", "STANDARD_PACK"])
def test_concealed_offers_are_noninterfering_in_final_requests(provider, phase):
    a = native_state(phase)
    area = "shop" if phase == "SHOP" else "pack"
    a[area]["cards"][0]["state"]["hidden"] = True
    b = deepcopy(a)
    b["bh"]["rng"] = "DIFFERENT_PRIVATE_RNG"
    card = b[area]["cards"][0]
    card.update(
        id=871, label="PRIVATE_SECRET_LABEL", set="TAROT", usable=True, min_targets=1, max_targets=2
    )
    card["value"] = {"rank": "A", "suit": "S", "effect": "PRIVATE_EFFECT"}
    card["modifier"] = {"edition": "PRIVATE_EDITION"}
    # Visible price and purchase affordance are unchanged; concealed identity differs.
    left, right = request(project(normalize(a)), provider), request(project(normalize(b)), provider)
    assert left == right
    view = delivered_observation(left, provider)
    (offer,) = view["state"]["offers"]
    assert offer["face_down"] and offer["rank"] is None and offer["suit"] is None
    assert offer["label"] == "Face-down card" and offer["effects"] == []
    assert "PRIVATE_" not in json.dumps(left)


def test_offer_handles_do_not_reconnect_across_concealment():
    raw = native_state()
    issuer = HandleIssuer(b"a" * 32)
    original = project(normalize(raw), issuer, 0).state.offers[0].id
    raw["shop"]["cards"][0]["state"]["hidden"] = True
    hidden = project(normalize(raw), issuer, 1).state.offers[0].id
    hidden_again = project(normalize(raw), issuer, 2).state.offers[0].id
    raw["shop"]["cards"][0]["state"]["hidden"] = False
    restored = project(normalize(raw), issuer, 3).state.offers[0].id
    assert len({original, hidden, hidden_again, restored}) == 4


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "phase,area", [("SHOP", "shop"), ("STANDARD_PACK", "pack"), ("SELECTING_HAND", "hand")]
)
def test_stone_card_underlying_rank_and_suit_do_not_reach_requests(provider, phase, area):
    raw = native_state(phase)
    stone = playing_card()
    stone.update(
        label="Stone Card",
        modifier={"enhancement": "STONE"},
        rank_visible=False,
        suit_visible=False,
    )
    stone["value"]["effect"] = "+50 chips; no rank or suit"
    raw[area] = {"cards": [stone]}
    before = project(normalize(raw))
    stone["value"]["rank"], stone["value"]["suit"] = "7", "C"
    after = project(normalize(raw))
    left, right = request(before, provider), request(after, provider)
    assert left == right
    visible = delivered_observation(left, provider)["state"][
        "hand" if area == "hand" else "offers"
    ][0]
    assert visible["label"] == "Stone Card" and not visible.get("rank") and not visible.get("suit")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blind_effects_rewards_status_and_owned_effects_are_distinct(provider):
    before = project(normalize(native_state("BLIND_SELECT")))
    view = delivered_observation(request(before, provider), provider)
    big, small, boss = view["state"]["revealed_blinds"]
    assert [b["status"] for b in (big, small, boss)] == ["SELECT", "DEFEATED", "UPCOMING"]
    assert big["effects"] == [] and small["skip_reward"] is None
    assert boss["effects"] == ["All face cards are debuffed"] and boss["skip_reward"] is None
    assert big["skip_reward"] == {
        "label": "Investment Tag",
        "effects": ["After defeating the Boss Blind, gain $25"],
        "acquisition_condition": "skip_this_blind",
    }
    assert view["state"]["owned_vouchers"][0]["label"] == "Seed Money"
    assert view["state"]["pending_tags"][0]["label"] == "Double Tag"
    assert view["state"]["persistent_effects"] == []
    action = ActionEnvelope.model_validate(
        {"observation_id": 0, "action": {"type": "select_blind", "blind_id": big["id"]}}
    )
    raw = native_state("SELECTING_HAND")
    raw["blinds"]["big"]["status"] = "CURRENT"
    after = project(normalize(raw), index=1)
    after.last_action = last_action(before, after, action.action)
    result = delivered_observation(request(after, provider), provider)
    assert "Investment Tag" not in json.dumps(result["state"]["pending_tags"])
    assert not any(c["path"] == ["pending_tags"] for c in result["last_action"]["changes"])
    assert result["state"]["revealed_blinds"][0]["status"] == "CURRENT"


def test_skip_reward_is_acquired_only_if_next_public_state_reports_it():
    raw = native_state("BLIND_SELECT")
    issuer = HandleIssuer(b"a" * 32)
    before = project(normalize(raw), issuer, 0)
    action = ActionEnvelope.model_validate(
        {
            "observation_id": 0,
            "action": {"type": "skip_blind", "blind_id": before.state.revealed_blinds[0].id},
        }
    ).action
    raw["bh"]["blind_on_deck"] = "Boss"
    raw["blinds"]["big"]["status"] = "SKIPPED"
    raw["bh"]["pending_tags"].append(
        {"label": "Investment Tag", "effects": ["After defeating the Boss Blind, gain $25"]}
    )
    after = project(normalize(raw), issuer, 1)
    delta = last_action(before, after, action)
    (acquired,) = [change for change in delta.changes if change.path == ["pending_tags"]]
    assert acquired.after[-1]["label"] == "Investment Tag"
    assert not any(tag["label"] == "Investment Tag" for tag in acquired.before)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_disabled_blind_effect_is_reported_only_for_the_active_blind(provider):
    raw = native_state("SELECTING_HAND")
    raw["bh"].update(blind_on_deck="Boss", blind_disabled=True)
    raw["blinds"]["boss"]["status"] = "CURRENT"
    view = delivered_observation(request(project(normalize(raw)), provider), provider)
    boss, big, small = view["state"]["revealed_blinds"]
    assert boss["effects"] == ["All face cards are debuffed"]
    assert boss["disabled"] is True and big["disabled"] is None and small["disabled"] is None
    raw["state"] = "BLIND_SELECT"
    assert all(b.disabled is None for b in project(normalize(raw)).state.revealed_blinds)


def test_last_action_preserves_departed_labels_without_promoting_agent_claims(store, config):
    class ClaimingPolicy(Baseline):
        def decide(self, ctx, exchanges):
            operation = super().decide(ctx, exchanges)
            if operation["kind"] == "action":
                operation["envelope"]["decision_note"] = "I scored a Royal Flush for 999999 points"
            return operation

    result = Runner(
        store,
        config,
        FakeGame(),
        ClaimingPolicy("heuristic"),
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run()
    observations = [
        Observation.model_validate(e["payload"])
        for e in store.events(result["episode_id"])
        if e["type"] == "observation"
    ]
    after_play = next(
        obs
        for obs in observations
        if obs.last_action and obs.last_action.action_type == "play_hand"
    )
    delta = after_play.last_action
    assert {c.label for c in delta.selected_objects} == {
        "Ace of Hearts",
        "Ace of Spades",
        "Two of Clubs",
    }
    assert not after_play.state.hand
    assert any(
        c.path == ["resources", "chips"] and c.before == "0" and c.after == "12"
        for c in delta.changes
    )
    assert "Royal Flush" not in delta.model_dump_json() and "999999" not in delta.model_dump_json()
    for provider in ("openai", "anthropic"):
        delivered = delivered_observation(request(after_play, provider), provider)
        assert delivered["last_action"] == delta.model_dump(mode="json")
        page = helper(
            Operation.validate_python(
                {"kind": "inspect_page", "section": "last_action", "offset": 0}
            ),
            [],
            {},
            after_play,
        )
        assert "Ace of Hearts" in page["content"]
    public = episode_export(store, result["episode_id"])
    saved = next(
        e["payload"]
        for e in public["events"]
        if e["type"] == "observation"
        and e["payload"]["observation_id"] == after_play.observation_id
    )
    assert saved["last_action"] == delta.model_dump(mode="json")
    checkpoint = json.loads(
        (
            store.episode_path(result["episode_id"], True)
            / f"checkpoint-{after_play.observation_id}.json"
        ).read_text()
    )
    assert checkpoint["observation"]["last_action"] == saved["last_action"]


def test_new_observations_are_versioned_and_old_ones_remain_readable():
    old = project(normalize(native_state())).model_dump(mode="json")
    old["schema_version"] = "1.0"
    old.pop("last_action")
    for key in ("owned_vouchers", "pending_tags"):
        old["state"].pop(key)
    restored = Observation.model_validate(old)
    assert restored.schema_version == "1.0" and restored.last_action is None
    assert restored.state.owned_vouchers is None and restored.state.pending_tags is None
    assert project(normalize(native_state())).schema_version == "1.1"


def test_branch_retains_known_last_action_without_recomputing_parent_future(store, episode, config):
    before = (store.episode_path(episode) / "events.jsonl").read_bytes()
    parent = next(
        e["payload"]
        for e in store.events(episode)
        if e["type"] == "observation" and e["observation_id"] == 2
    )
    assert parent["last_action"]["action_type"] == "play_hand"
    assert verify_checkpoint(store, config, episode, 2)["status"] == "passed"
    service = RunService(store, ReviewService(store))
    child = service.branch(config, episode, 2, "agent_continue")
    service.thread.join(10)
    assert not service.thread.is_alive()
    first = next(e["payload"] for e in store.events(child) if e["type"] == "observation")
    assert first["last_action"] == parent["last_action"]
    assert first["episode_id"] == child
    assert (store.episode_path(episode) / "events.jsonl").read_bytes() == before


def test_last_action_remains_behind_prospective_consequence_gate(store, episode):
    review = ReviewService(store)
    opened = review.open(episode)
    assert opened["view"]["observation"]["last_action"] is None
    token = opened["review_token"]
    action_view = review.advance(token)
    assert action_view["observation"]["last_action"] is None
    assert "transition" not in action_view
    consequences = review.advance(token)
    assert consequences["transition"]["last_action"]["action_type"] == "select_blind"
