-- Shared post-mutation settling. Both bh_action and upstream mutators use
-- this exact queue: thirty transition frames, then ten consecutive ready
-- frames. The dispatcher keeps busy/active_id asserted until completion or
-- an unknown-status timeout.
local settle = {}
  local TRANSITION_FRAMES = 30
  local STABLE_READY_FRAMES = 10
  -- The pinned bridge runs at 60 FPS: 15 seconds is well below its 90-second
  -- HTTP deadline, while leaving ample room for the normal 30+10 frames.
  local MAX_SETTLE_FRAMES = 900
  local pending
  local original_update
  local running = false

  function settle.install(ready, on_timeout)
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
      elseif pending.age >= MAX_SETTLE_FRAMES then
        pending = nil
        on_timeout()
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
