"""Execute project-owned extraction in Lua 5.1 with stubbed UI; never launch LÖVE."""

import json

import pytest
from lupa.lua51 import LuaRuntime
from test_boundary import project
from test_public_information import delivered_observation, native_state, request

from balatro_horizons.config import ROOT
from balatro_horizons.game.state import normalize


@pytest.fixture
def lua():
    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute("""
      removed = 0
      G = {
        GAME = {used_vouchers={v_seed_money=true}, tags={},
          round_resets={ante=2,blind_tags={Small='tag_orbital',Big='tag_investment'}},
          orbital_choices={[2]={Small='Flush'}},hands_played=7},
        P_CENTERS = {v_seed_money={key='v_seed_money',set='Voucher',name='Seed Money',config={extra=10}}},
        P_TAGS = {
          tag_orbital={key='tag_orbital',set='Tag',name='Orbital Tag',config={levels=3}},
          tag_investment={key='tag_investment',set='Tag',name='Investment Tag',config={dollars=25}},
          tag_handy={key='tag_handy',set='Tag',name='Handy Tag',config={dollars_per_hand=1}}
        }
      }
      function pseudorandom() error('must not touch RNG') end
      function pseudoseed() error('must not touch RNG') end
      SMODS = {
        has_playing_card_property=function(card, property)
          assert(property == 'replace_base_card')
          return card.ability and card.ability.name == 'Stone Card'
        end,
        has_no_rank=function() return false end,
        has_no_suit=function() return false end,
      }
      Card = function() error('must not construct a card') end
      Tag = setmetatable({}, {__call=function() error('must not construct a tag') end})
      function Tag.get_uibox_table(tag, sprite, vars_only)
        assert(sprite == nil and vars_only == true)
        if tag.key == 'tag_orbital' then return {tag.ability.orbital_hand,tag.config.levels} end
        if tag.key == 'tag_investment' then return {tag.config.dollars} end
        return {G.GAME.hands_played}
      end
      function localize(args)
        if type(args)=='string' then return 'Poker Hand' end
        assert(args.type=='name_text')
        return (G.P_CENTERS[args.key] or G.P_TAGS[args.key]).name
      end
      function generate_card_ui(center, _, vars)
        local text
        if center.key == 'v_seed_money' then text='Raise interest cap to $'..center.config.extra
        elseif center.key == 'tag_orbital' then text='Upgrade '..vars[1]..' by '..vars[2]..' levels'
        elseif center.key == 'tag_investment' then text='After defeating the Boss Blind, gain $'..vars[1]
        else text='Gain $'..vars[1] end
        return {main={{{nodes={{config={text=text}}}}}},
          info={{config={text='PRIVATE_AUXILIARY_INFO',object={remove=function() removed=removed+1 end}}}}}
      end
    """)
    public = runtime.execute((ROOT / "native/patches/horizons_public.lua").read_text())
    runtime.globals().public = public
    return runtime


def test_lua_label_override_preserves_playing_card_identity(lua):
    lua.execute("""
      local out = {label='WRONG',value={rank='K',suit='H'}}
      public.card_label({ability={name='Bonus Card'}},out)
      assert(out.label == nil and out.value.rank == 'K' and out.value.suit == 'H')
      local joker = {label='WRONG',value={effect='+4 Mult'}}
      public.card_label({ability={name='Joker'}},joker)
      assert(joker.label == 'Joker' and joker.value.effect == '+4 Mult')
    """)


def test_lua_stone_card_suppresses_underlying_base_identity(lua):
    lua.execute("""
      local card = {ability={name='Stone Card'},base={value='K',suit='Hearts'}}
      local out = {label='K of Hearts',value={rank='K',suit='H'}}
      public.card_label(card,out)
      assert(out.label == 'Stone Card')
      assert(out.value.rank == nil and out.value.suit == nil)
      assert(out.rank_visible == false and out.suit_visible == false)
      assert(card.base.value == 'K' and card.base.suit == 'Hearts')
      assert(public.deck_key(card) == 'No suit:No rank')
      card.base.value = '7'; card.base.suit = 'Clubs'
      assert(public.deck_key(card) == 'No suit:No rank')
      assert(public.deck_key({ability={name='Base Card'},base={value='K',suit='Hearts'}}) == 'Hearts:K')
    """)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_lua_descriptions_reach_serialized_request_without_keys_or_auxiliary_ui(lua, provider):
    lua.execute("""
      G.GAME.tags = {{key='tag_handy',name='Handy Tag',config={},ability={}},
                    {key='tag_investment',triggered=true},
                    {key='PRIVATE_HIDDEN_TAG',hide_ability=true}}
      G.GAME.blind = {disabled=true}
      state = {bh={tags={'PRIVATE_CONTINUATION_KEY'}},blinds={small={},big={},boss={}}}
      public.extend(state)
      assert(state.bh.blind_disabled == true)
      assert(#state.bh.pending_tags == 2)
      assert(state.bh.pending_tags[2].label == 'Hidden tag')
      assert(state.bh.tags[1] == 'PRIVATE_CONTINUATION_KEY')
      assert(state.blinds.small.tag_effect == 'Upgrade Flush by 3 levels')
      assert(state.blinds.big.tag_effect == 'After defeating the Boss Blind, gain $25')
      assert(removed == 4) -- Generated UI objects are cleaned up, including auxiliary text.
      assert(G.GAME.hands_played == 7 and G.GAME.used_vouchers.v_seed_money)
      assert(#G.GAME.tags == 3)
    """)
    result = lua.globals().state

    def effect(item):
        return {"label": item.label, "effects": list(item.effects.values())}

    raw = native_state("BLIND_SELECT")
    raw["bh"]["owned_vouchers"] = [effect(item) for item in result.bh.owned_vouchers.values()]
    raw["bh"]["pending_tags"] = [effect(item) for item in result.bh.pending_tags.values()]
    for name in ("small", "big"):
        raw["blinds"][name]["tag_name"] = result.blinds[name].tag_name
        raw["blinds"][name]["tag_effect"] = result.blinds[name].tag_effect
    body = request(project(normalize(raw)), provider)
    view = delivered_observation(body, provider)
    assert view["state"]["owned_vouchers"] == [
        {"label": "Seed Money", "effects": ["Raise interest cap to $10"]}
    ]
    assert view["state"]["pending_tags"] == [
        {"label": "Handy Tag", "effects": ["Gain $7"]},
        {"label": "Hidden tag", "effects": []},
    ]
    assert "Upgrade Flush by 3 levels" in json.dumps(body)
    assert "PRIVATE_" not in json.dumps(body)


def test_all_project_lua_patches_compile_as_lua51(lua):
    for path in (ROOT / "native/patches").glob("*.lua"):
        lua.compile(path.read_text(), name=path.name)
