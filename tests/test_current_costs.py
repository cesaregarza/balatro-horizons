import json
from copy import deepcopy

import pytest
from test_boundary import project
from test_public_information import native_state, request

from balatro_horizons.contracts import PublicCard
from balatro_horizons.game.state import normalize
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.context.game_quotes import current_costs
from balatro_horizons.harness.transport import context_payload


def prices(body, provider):
    messages = body["input" if provider == "openai" else "messages"]
    return json.loads(next(m for m in messages if m.get("role") == "user")["content"])[
        "current_costs"
    ]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_live_quotes_change_without_changing_tools_or_cached_instructions(provider):
    obs = project(normalize(native_state()))
    obs.state.jokers = [
        PublicCard(id="chaos", label="Chaos the Clown", effects=["1 free Reroll per shop"])
    ]
    original = obs.model_dump(mode="json")
    obs.state.resources.shop_reroll_cost = "0"
    first = request(obs, provider)
    obs.state.resources.shop_reroll_cost = "5"
    obs.observation_id += 1
    second = request(obs, provider)
    assert prices(first, provider)["rerolls"]["reroll_shop"]["cash_cost"] == "0"
    assert prices(second, provider)["rerolls"]["reroll_shop"]["cash_cost"] == "5"
    assert prices(second, provider)["offers"][0]["cash_cost"] == "7"
    assert first["tools"] == second["tools"]
    if provider == "openai":
        assert first["input"][0] == second["input"][0]
        assert first["input"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert json.dumps(second).count('"prompt_cache_breakpoint"') == 1
    else:
        assert first["system"] == second["system"]
    assert "current_costs" not in json.dumps(original)


def test_cost_summary_preserves_unknown_prices_and_masks_concealed_items():
    obs = project(normalize(native_state()))
    obs.state.resources.shop_reroll_cost = None
    obs.state.offers[0].face_down = True
    obs.state.offers[0].label = "PRIVATE_CONCEALED_LABEL"
    source = obs.model_dump(mode="json")
    before = deepcopy(source)
    summary = current_costs(source)
    assert summary["rerolls"]["reroll_shop"]["cash_cost"] is None
    assert summary["offers"][0]["cash_cost"] == "7"
    assert "PRIVATE_CONCEALED_LABEL" not in json.dumps(summary)
    assert source == before


def test_pack_and_boss_quotes_do_not_reuse_shop_prices():
    obs = project(normalize(native_state("STANDARD_PACK")))
    summary = current_costs(obs.model_dump(mode="json"))
    assert "rerolls" not in summary
    assert summary["offers"][0]["operation"] == "choose_pack"
    assert summary["offers"][0]["cash_cost"] == "0"
    obs = project(normalize(native_state("BLIND_SELECT")))
    obs.state.resources.boss_reroll_cost = "10"
    obs.available_action_types.append("reroll_boss")
    summary = current_costs(obs.model_dump(mode="json"))
    assert summary["rerolls"]["reroll_boss"]["cash_cost"] == "10"
    assert summary["rerolls"]["reroll_boss"]["offered"] is True
    assert summary["rerolls"]["reroll_boss"]["mode_check"] == {"status": "legal"}
    assert "offers" not in summary


def test_sale_values_are_proceeds_and_hidden_cards_do_not_gain_quotes():
    obs = project(normalize(native_state()))
    obs.available_action_types.append("sell")
    obs.state.jokers = [
        PublicCard(id="visible", label="Drunkard", sellable=True, sell_price="3"),
        PublicCard(id="eternal", label="Joker", sellable=False, sell_price="9"),
        PublicCard(
            id="hidden", label="PRIVATE_CARD", face_down=True, sellable=True, sell_price="99"
        ),
    ]
    owned = current_costs(obs.model_dump(mode="json"))["owned_items"]
    assert [c["owned_id"] for c in owned] == ["visible", "eternal"]
    assert owned[0]["quoted_sale_proceeds"] == "3" and owned[0]["sell_allowed"]
    assert owned[0]["mode_check"] == {"status": "legal"}
    assert owned[1]["quoted_sale_proceeds"] == "9" and not owned[1]["sell_allowed"]
    assert owned[1]["mode_check"]["reason"] == "SELL_NOT_AVAILABLE"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_costs_survive_helper_followups_and_all_initial_requests(provider):
    obs = project(normalize(native_state()))
    exchange = {
        "operation": {"kind": "inspect_page", "section": "offers", "offset": 0},
        "tool_call": {"name": "inspect_state", "arguments": {"section": "offers", "offset": 0}},
        "result": {"content": "public offer details", "game_advanced": False},
    }
    ctx, delivered = decision_context(obs, [exchange])
    body = context_payload(ctx, delivered, provider)
    assert prices(body, provider)["rerolls"]["reroll_shop"]["cash_cost"] == "5"
    assert "public offer details" in json.dumps(body)
    assert prices(request(obs, provider), provider) == prices(body, provider)
