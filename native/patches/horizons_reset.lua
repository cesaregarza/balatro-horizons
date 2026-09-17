-- Evaluator-only new-game reset. Gameplay actions and paid episodes never call it.
-- Menu/start resets G.GAME, but profile statistics and native card IDs are global.
local reset = {}
local baseline = nil

local function clone(value, seen)
  if type(value) ~= 'table' then return value end
  seen = seen or {}
  if seen[value] then return seen[value] end
  local result = {}
  seen[value] = result
  for key, item in pairs(value) do result[key] = clone(item, seen) end
  return setmetatable(result, getmetatable(value))
end

function reset.before_start()
  if os.getenv('BH_CALIBRATION') ~= '1' then return end
  assert(G.STATE == G.STATES.MENU, 'Calibration reset requires menu')
  local profile_id = G.SETTINGS.profile
  assert(type(G.PROFILES[profile_id]) == 'table', 'Calibration profile missing')
  if not baseline then
    -- Capture after the native startup/profile initialization, just before the
    -- first scripted start. A fresh process establishes its own baseline.
    baseline = {
      profile_id=profile_id, profile=clone(G.PROFILES[profile_id]),
      sort_id=G.sort_id, playing_card=G.playing_card,
    }
  end
  assert(profile_id == baseline.profile_id, 'Calibration profile changed')
  G.PROFILES[profile_id] = clone(baseline.profile)
  G.sort_id = baseline.sort_id
  G.playing_card = baseline.playing_card
end

return reset
