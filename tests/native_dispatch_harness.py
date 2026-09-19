"""Offline Lua harness for the private native dispatcher patch."""

from lupa.lua51 import LuaRuntime

from balatro_horizons.config import ROOT

BOOTSTRAP = r"""
local function dump(value, seen)
  local kind = type(value)
  if kind == 'nil' then return 'nil' end
  if kind == 'string' then return string.format('%q', value) end
  if kind ~= 'table' then return kind..':'..tostring(value) end
  seen = seen or {}
  if seen[value] then return '<seen>' end
  seen[value] = true
  local keys = {}
  for key in pairs(value) do keys[#keys+1] = key end
  table.sort(keys, function(left, right)
    return type(left)..':'..tostring(left) < type(right)..':'..tostring(right)
  end)
  local parts = {}
  for _, key in ipairs(keys) do
    parts[#parts+1] = dump(key, seen)..'='..dump(value[key], seen)
  end
  return '{'..table.concat(parts, ',')..'}'
end

package.preload.json = function()
  return {encode=function(value) return dump(value, {}) end, decode=function() return {} end}
end

responses = {}
upstream_requests = {}
call_log = {}
persist_count = 0
persist_payload = nil
reset_calls = 0
auto_respond = true
next_response = nil
pack_space = true
inspect_marker = 'INSPECTED_STATE'

function pseudorandom() error('must not touch RNG') end
function pseudoseed() error('must not touch RNG') end

function make_area(cards, limit)
  local result = {
    cards=cards or {}, highlighted={},
    config={card_limit=limit or 5, highlighted_limit=5},
  }
  function result:unhighlight_all() self.highlighted = {} end
  function result:add_to_highlighted(card) self.highlighted[#self.highlighted+1] = card end
  return result
end

function make_card(options)
  options = options or {}
  local card = {
    id=options.id or 'card', cost=options.cost or 0,
    ability={set=options.set or 'Joker', consumeable=options.consumeable or false},
    config={center={key=options.key or 'center', config=options.requirements or {}}},
    edition=options.edition, forced_selection=options.forced_selection or false,
  }
  function card:can_sell_card() return options.sellable ~= false end
  function card:can_use_consumeable() return options.usable ~= false end
  return card
end

function highlighted_ids()
  local result = {}
  for _, card in ipairs(G.hand.highlighted or {}) do result[#result+1] = card.id end
  return result
end

local function record_action(name, event)
  call_log[#call_log+1] = {
    name=name,
    card=event and event.config and event.config.ref_table or nil,
    mode=event and event.config and event.config.id or nil,
    highlighted=highlighted_ids(),
  }
end

G = {
  STATE=3,
  STATES={MENU=1, BLIND_SELECT=2, SHOP=3, GAME_OVER=99, ROUND_EVAL=100},
  STATE_COMPLETE=true,
  CONTROLLER={locked=false},
  SETTINGS={profile=1},
  PROFILES={[1]={name='test-profile'}},
  GAME={
    won=false, STOP_USE=0, dollars=20, bankrupt_at=0, pack_choices=1,
    blind={chips=100}, blind_on_deck='Big', used_vouchers={},
    round_resets={boss_rerolled=false}, tags={}, pseudorandom='RNG_SENTINEL', round=1,
  },
  FUNCS={},
  P_CENTERS={}, P_BLINDS={}, P_TAGS={},
  localization={descriptions={}},
  playing_cards={},
  VERSION='test-version',
}
G.hand = make_area({}, 5)
G.jokers = make_area({}, 5)
G.consumeables = make_area({}, 2)
G.shop_jokers = make_area({}, 5)
G.shop_vouchers = make_area({}, 5)
G.shop_booster = make_area({}, 5)
G.pack_cards = make_area({}, 5)

G.FUNCS.sell_card = function(event) record_action('sell_card', event) end
G.FUNCS.buy_from_shop = function(event) record_action('buy_from_shop', event) end
G.FUNCS.use_card = function(event) record_action('use_card', event) end
G.FUNCS.play_cards_from_highlighted = function(event)
  record_action('play_cards_from_highlighted', event)
end
G.FUNCS.discard_cards_from_highlighted = function(event)
  record_action('discard_cards_from_highlighted', event)
end
G.FUNCS.skip_booster = function(event) record_action('skip_booster', event) end
G.FUNCS.reroll_boss = function(event) record_action('reroll_boss', event) end
G.FUNCS.check_for_buy_space = function() return pack_space end
G.FUNCS.evaluate_round = function() end

BH_ISOLATION = true
BB_SETTINGS = {headless=false, fast=false}
BB_ERROR_NAMES = {NOT_ALLOWED='NOT_ALLOWED_NAME'}
BB_GAMESTATE = {get_gamestate=function() return {marker=inspect_marker} end}

love = {
  update=function() end,
  filesystem={
    getIdentity=function() return 'BalatroHorizons' end,
    getSaveDirectory=function() return '/isolated-save' end,
  },
}

os.getenv = function(name)
  local values = {
    BH_TOKEN='secret-token', BH_RUNTIME='/isolated-runtime',
    BH_INSTANCE_ID='instance', BH_CALIBRATION='0',
  }
  return values[name]
end

io.open = function(path, mode)
  local file = {}
  function file:read() return '{}' end
  function file:write(value) persist_count=persist_count+1; persist_payload=value end
  function file:flush() end
  function file:close() end
  return file
end

local public = {
  card_label=function() end,
  deck_key=function(card) return tostring(card.id) end,
  extend=function() end,
}
local settlement = {install=function() end, visible=function() return nil end}
local reset = {before_start=function() reset_calls=reset_calls+1 end}
SMODS = {
  load_file=function(name)
    if name == 'horizons_public.lua' then return function() return public end end
    if name == 'horizons_settlement.lua' then return function() return settlement end end
    if name == 'horizons_reset.lua' then return function() return reset end end
    if name == 'horizons_fixtures.lua' then
      return function() return function() end end
    end
    error('unexpected module '..tostring(name))
  end,
}

registered = {}
BB_DISPATCHER = {}
BB_DISPATCHER.register = function(spec) registered[spec.name]=spec; return true end
BB_SERVER = {}
BB_SERVER.send_response = function(response)
  responses[#responses+1] = response
  return response
end
BB_DISPATCHER.dispatch = function(request)
  upstream_requests[#upstream_requests+1] = request
  local handler = registered[request.method]
  if handler then
    handler.execute(request.params or {}, function(response) BB_SERVER.send_response(response) end)
  elseif auto_respond then
    BB_SERVER.send_response(next_response or {ok=true, method=request.method})
  end
end

function snapshot_g() return dump(G, {}) end
function response_count() return #responses end
function upstream_count() return #upstream_requests end
function action_count() return #call_log end
function advance(frames)
  for _=1,frames do love.update(0.016) end
end
"""


class NativeHarness:
    def __init__(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(BOOTSTRAP)
        self.lua.execute((ROOT / "native/patches/horizons.lua").read_text())

    @property
    def globals(self):
        return self.lua.globals()

    def execute(self, source):
        return self.lua.execute(source)

    def eval(self, source):
        return self.lua.eval(source)

    def request(self, method, params=None, *, request_id=1, token="secret-token"):
        before = self.eval("response_count()")
        payload = dict(params or {})
        payload["_bh_token"] = token
        request = self.lua.table_from(
            {"id": request_id, "method": method, "params": payload}, recursive=True
        )
        self.globals.BB_DISPATCHER.dispatch(request)
        if self.eval("response_count()") == before:
            return None
        return self.globals.responses[self.eval("response_count()")]

    def status(self, request_id):
        return self.request(
            "bh_request_status", {"request_id": str(request_id)}, request_id=f"status-{request_id}"
        )

    def snapshot(self):
        return self.eval("snapshot_g()")

    def advance(self, frames):
        self.globals.advance(frames)


def assert_error_unchanged(harness, code, method, params=None, **request_options):
    before = harness.snapshot()
    response = harness.request(method, params, **request_options)
    assert response.message == code
    assert harness.snapshot() == before
    return response
