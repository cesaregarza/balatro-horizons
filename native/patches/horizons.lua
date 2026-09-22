-- Project-owned evaluator instrumentation for the pinned BalatroBot bridge.
-- The implementation is split by concern; this file is intentionally only the
-- bootstrap manifest and must not expose an endpoint directly to providers.
local json = require('json')
local public = assert(SMODS.load_file('horizons_public.lua'))()
local native_settlement = assert(SMODS.load_file('horizons_settlement.lua'))()
local reset = assert(SMODS.load_file('horizons_reset.lua'))()
local function load_piece(name)
  local piece = assert(SMODS.load_file(name))()
  return type(piece) == 'function' and piece() or piece
end
local inspect = load_piece('inspect.lua')
local settle = load_piece('settle.lua')
local action = load_piece('action.lua')
local dispatch = load_piece('dispatch.lua')

native_settlement.install()

local token = assert(os.getenv('BH_TOKEN'), 'Missing private bridge token')
local runtime = assert(os.getenv('BH_RUNTIME'), 'Missing isolated runtime directory')
local manifest_file = assert(io.open(runtime .. '/environment.lock.json', 'r'))
local loaded_manifest = json.decode(manifest_file:read('*a'))
manifest_file:close()
assert(BH_ISOLATION and love.filesystem.getIdentity() == 'BalatroHorizons',
  'Save isolation not established')

-- Fresh isolated profiles have no tutorial progress; mark tutorial completion
-- before main_menu. These settings are native startup policy, not game logic.
G.SETTINGS.tutorial_complete = true
G.SETTINGS.tutorial_progress = nil
G.SETTINGS.crashreports = false
G.F_CRASH_REPORTS = false

local state = {
  json = json,
  public = public,
  native_settlement_visible = native_settlement.visible,
  reset = reset,
  inspect = inspect,
  settle = settle,
  action = action,
  dispatch = dispatch,
  token = token,
  runtime = runtime,
  loaded_manifest = loaded_manifest,
}

dispatch.install(state)
