import json
from copy import deepcopy

import pytest
from lupa.lua51 import LuaRuntime
from test_boundary import project
from test_public_information import delivered_observation, native_state, request

from balatro_horizons.config import ROOT
from balatro_horizons.engine.native_state import normalize
from balatro_horizons.evaluation.economy import economy_metrics


def test_cashout_hooks_preserve_native_calls_and_omit_hidden_rows():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
      G={STATE=1,STATES={ROUND_EVAL=1},GAME={round=3,interest_amount=1,interest_cap=50,
        modifiers={},blind={name='Small Blind'}},FUNCS={}}
      calls={}
      function localize(config)
        assert(config.key=='interest')
        assert(config.vars[1]==1 and config.vars[2]==5 and config.vars[3]==10)
        return 'Interest: $1 per $5, maximum $10'
      end
      function add_round_eval_row(config) calls[#calls+1]=config; return 'unchanged' end
      function G.FUNCS.evaluate_round()
        assert(add_round_eval_row({name='blind1',dollars=3})=='unchanged')
        add_round_eval_row({name='interest',dollars=8})
        for i=1,7 do add_round_eval_row({name='custom'..i,dollars=i,text=i>5 and 'PRIVATE_HIDDEN_ROW' or 'Visible reward'}) end
        add_round_eval_row({name='bottom',dollars=39})
        return 42
      end
      function pseudorandom() error('must not use RNG') end
    """)
    module = lua.execute((ROOT / "native/patches/horizons_settlement.lua").read_text())
    module.install()
    assert module.visible() is None
    assert lua.eval("G.FUNCS.evaluate_round()") == 42
    value = module.visible()
    assert len(value.rows) == 7 and value.omitted_rows == 2 and value.total == "39"
    assert value.rows[2].label == "Interest: $1 per $5, maximum $10"
    assert len(lua.globals().calls) == 10
    assert all("PRIVATE" not in row.label for row in value.rows.values())
    lua.execute("G.STATE=2")
    assert module.visible() is None
    lua.execute("G.STATE=1; G.GAME.round=4")
    assert module.visible() is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_settlement_survives_public_projection_and_request_without_private_fields(provider):
    raw = native_state("ROUND_EVAL")
    raw["bh"]["settlement"] = {
        "source": "native_cashout_rows",
        "total": "7",
        "omitted_rows": 0,
        "private_seed": "PRIVATE_SENTINEL",
        "rows": [
            {
                "kind": "blind",
                "label": "Small Blind",
                "dollars": "3",
                "private": "PRIVATE_SENTINEL",
            },
            {"kind": "hands", "label": "Remaining hands ($1 each)", "dollars": "3", "count": "3"},
            {"kind": "interest", "label": "Interest ($1 per $5, max $5)", "dollars": "1"},
        ],
    }
    obs = project(normalize(raw))
    body = request(obs, provider)
    view = delivered_observation(body, provider)
    assert view["state"]["settlement"]["rows"][2]["dollars"] == "1"
    assert "PRIVATE_SENTINEL" not in json.dumps(body)
    raw["state"] = "SHOP"
    assert project(normalize(raw)).state.settlement is None


def test_economy_metrics_distinguish_zero_unknown_and_committed_cashouts():
    obs = project(normalize(native_state("ROUND_EVAL"))).model_dump(mode="json")
    obs["state"]["resources"].update(money="-4", chips="600", target="300")
    obs["state"]["settlement"] = {
        "source": "native_cashout_rows",
        "rows": [],
        "total": "7",
        "omitted_rows": 0,
    }

    def pair(index, source):
        source = deepcopy(source)
        source["observation_id"] = index
        return [
            {"type": "observation", "payload": source},
            {
                "type": "action_commit",
                "payload": {"observation_id": index, "action": {"type": "cash_out"}},
            },
        ]

    events = pair(0, obs)
    obs["state"]["settlement"]["omitted_rows"] = 2
    events += pair(1, obs)
    obs["state"]["settlement"] = None
    events += pair(2, obs)
    # An observed cash-out without a committed action is not counted twice.
    events += pair(3, obs)[:1]
    metrics = economy_metrics(events)
    assert metrics["settlements_with_known_interest"] == 1
    assert metrics["settlements_with_unknown_interest"] == 2
    assert metrics["known_interest_total"] == "0"
    assert metrics["settlements"][0]["score_target_ratio"] == "2"
    obs["phase"] = "SELECTING_HAND"
    events += pair(4, obs)[:1] + pair(5, obs)[:1]
    assert economy_metrics(events)["played_rounds_observed_in_debt"] == 1
