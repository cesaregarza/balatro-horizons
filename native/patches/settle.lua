-- Shared post-mutation settling. Both bh_action and upstream mutators use
-- this exact queue: thirty transition frames, then ten consecutive ready
-- frames. The dispatcher keeps busy/active_id asserted until the callback.
local settle = {}
  local TRANSITION_FRAMES = 30
  local STABLE_READY_FRAMES = 10
  local pending
  local original_update
  local running = false

  function settle.install(ready)
    original_update = love.update
    love.update = function(dt)
      original_update(dt)
      if not pending then return end
      pending.age = pending.age + 1
      if pending.age > TRANSITION_FRAMES and ready() then
        pending.stable = pending.stable + 1
      else
        pending.stable = 0
      end
      if pending.stable >= STABLE_READY_FRAMES then
        local callback = pending.callback
        pending = nil
        running = true
        callback()
        running = false
      end
    end
  end

  function settle.defer(callback)
    assert(not pending, 'settlement already pending')
    pending = {callback=callback, age=0, stable=0}
  end

  function settle.pending() return pending ~= nil end
  function settle.running() return running end

return settle
