-- Must execute before main.lua requires any game code or starts save threads.
assert(os.getenv('BH_ISOLATED_RUNTIME') == '1', 'Horizons requires its isolated launcher')
local identity = 'BalatroHorizons'
love.filesystem.setIdentity(identity, false)
assert(love.filesystem.getIdentity() == identity, 'Horizons save isolation failed')
local original_set_identity = love.filesystem.setIdentity
love.filesystem.setIdentity = function(requested, append)
  assert(requested == identity, 'Attempt to change isolated save identity')
  return original_set_identity(identity, append)
end
-- Isolated runs must never contact personal Steam services or upload crashes.
package.preload['luasteam'] = function() return {init=function() return false end} end
BH_ISOLATION = {identity=identity, save_directory=love.filesystem.getSaveDirectory()}
-- Always initialize from the game's default profile/settings. Prior isolated runs
-- may write statistics, but those files never influence a subsequent slot.
local original_read, original_info = love.filesystem.read, love.filesystem.getInfo
local function is_profile_input(path)
  return type(path) == 'string' and (path == 'settings.jkr' or
    path:match('^%d+/[^/]+%.jkr$'))
end
love.filesystem.read = function(path, ...)
  if is_profile_input(path) then return nil, 'Horizons frozen fresh profile' end
  return original_read(path, ...)
end
love.filesystem.getInfo = function(path, ...)
  if is_profile_input(path) then return nil end
  return original_info(path, ...)
end
