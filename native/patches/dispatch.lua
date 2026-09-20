-- Private dispatcher admission, request ledger, and upstream bridge wrapping.
local dispatch = {}
  local action_codes = {
    UNKNOWN_CARD=true, INVALID_TARGET_COUNT=true, INVALID_TARGETS=true,
    SELL_NOT_AVAILABLE=true, UNAFFORDABLE=true, CAPACITY=true,
    USE_NOT_AVAILABLE=true, INVALID_CARD_COUNT=true, INVALID_SELECTION=true,
    FORCED_CARD_REQUIRED=true, PACK_NOT_OPEN=true, REROLL_NOT_AVAILABLE=true,
    UNKNOWN_ACTION=true,
  }
  local infrastructure_codes = {
    NOT_READY=true, BUSY=true, ACTION_STATUS_UNKNOWN=true, REQUEST_ID_REUSED=true,
  }
  local harness_codes = {UNAUTHORIZED=true, METHOD_FORBIDDEN=true, PATH_FORBIDDEN=true}
  local reads = {bh_inspect=true, bh_request_status=true, bh_rules=true}
  -- Keep this list synchronized with the Python and PowerShell bridges.
  local allowed = {
    bh_inspect=true, bh_request_status=true, bh_rules=true,
    bh_action=true, bh_fixture=true, start=true, menu=true, save=true,
    load=true, select=true, skip=true, cash_out=true, next_round=true,
    reroll=true, rearrange=true,
  }

  local function error_name(code)
    local kind = action_codes[code] and 'NOT_ALLOWED' or
      infrastructure_codes[code] and 'INFRASTRUCTURE' or
      harness_codes[code] and 'HARNESS_FAULT' or 'INFRASTRUCTURE'
    return (BB_ERROR_NAMES and BB_ERROR_NAMES[kind]) or kind
  end

  local function respond_error(send, code)
    send({message=code, name=error_name(code)})
  end

  local function persist_ledger(state)
    local file = assert(io.open(state.runtime .. '/request-ledger.json', 'w'))
    file:write(state.json.encode(state.ledger)); file:flush(); file:close()
  end

  local function unlock()
    for _, pool in ipairs({G.P_CENTERS, G.P_BLINDS, G.P_TAGS}) do
      for _, center in pairs(pool or {}) do
        center.unlocked = true
        center.discovered = true
      end
    end
  end

  local function register(name, schema, execute)
    assert(BB_DISPATCHER.register({name=name,
      description='Horizons private evaluator', schema=schema, execute=execute}))
  end

  local function complete(state, response)
    if state.active_id then
      state.ledger[state.active_id] = {
        status=response.message and 'rejected' or 'committed', response=response,
        intent=state.ledger[state.active_id].intent,
      }
      persist_ledger(state)
      state.active_id = nil
      state.busy = false
    end
    return state.original_send(response)
  end

  local function expire(state)
    local id = state.active_id
    if not id then return end
    -- A timed-out mutation may have changed the game. Never turn it into a
    -- committed or rejected result, even if it becomes ready later.
    state.ledger[id] = {status='unknown', intent=state.ledger[id].intent}
    persist_ledger(state)
    state.active_id = nil
    state.busy = false
    respond_error(state.original_send, 'ACTION_STATUS_UNKNOWN')
  end

  local function install_inspection(state)
    local inspect = state.context.inspect
    inspect.set_context({
      public=state.context.public, loaded_manifest=state.context.loaded_manifest,
      busy=function() return state.busy end,
      settlement_visible=function() return state.context.native_settlement_visible and
        state.context.native_settlement_visible() or nil end,
    })
    state.context.settle.install(inspect.ready, function() expire(state) end)
  end

  local function register_endpoints(state)
    local inspect = state.context.inspect
    register('bh_inspect', {}, function(_, send) send(inspect.read()) end)
    assert(SMODS.load_file('horizons_fixtures.lua'))()(register, inspect.read)
    register('bh_request_status', {request_id={type='string',required=true}}, function(args, send)
      send(state.ledger[args.request_id] or {status='unknown'})
    end)
    register('bh_rules', {}, function(_, send)
      local rules = {}
      for set, items in pairs(G.localization.descriptions or {}) do
        for key, value in pairs(items) do
          rules[key] = {set=set, name=value.name, text=value.text}
        end
      end
      send({rules=rules})
    end)
    state.context.action.install(register, {inspect=inspect, settle=state.context.settle,
      respond_error=respond_error})
  end

  local function checkpoint_path_ok(runtime, path)
    if type(path) ~= 'string' then return false end
    local root = runtime:gsub('\\', '/'):gsub('/+$', '')
    local prefix = root .. '/checkpoints/'
    if path:sub(1, #prefix) ~= prefix then return false end
    return path:sub(#prefix + 1):match('^[a-f0-9]+%.jkr$') ~= nil
  end

  local function install_response_wrapper(state)
    state.original_send = BB_SERVER.send_response
    BB_SERVER.send_response = function(response)
      if state.active_id and not state.servicing_read then
        if response.message or state.context.settle.running() then
          return complete(state, response)
        end
        -- Hold upstream successes on the same pending queue as bh_action.
        state.context.settle.defer(function() complete(state, response) end)
        return response
      end
      return state.original_send(response)
    end
  end

  local function replay_or_admit(state, request, intent)
    local id = tostring(request.id)
    local record = state.ledger[id]
    if record then
      if record.intent ~= intent then
        respond_error(state.original_send, 'REQUEST_ID_REUSED')
      -- Replays bypass busy and settlement. A pending replay is uncertain.
      elseif record.response then state.original_send(record.response)
      else respond_error(state.original_send, 'ACTION_STATUS_UNKNOWN') end
      return false
    end
    if state.busy then respond_error(state.original_send, 'BUSY'); return false end
    if request.method == 'start' and G.STATE == G.STATES.MENU then
      state.context.reset.before_start()
    end
    unlock()
    state.busy = true
    state.active_id = id
    state.ledger[id] = {status='pending', intent=intent}
    persist_ledger(state)
    return true
  end

  local function route_request(state, request)
    local params = request.params or {}
    if params._bh_token ~= state.context.token then
      respond_error(state.original_send, 'UNAUTHORIZED'); return
    end
    params._bh_token = nil
    request.params = params
    if not allowed[request.method] then
      respond_error(state.original_send, 'METHOD_FORBIDDEN'); return
    end
    if (request.method=='save' or request.method=='load') and
        not checkpoint_path_ok(state.runtime, params.path) then
      respond_error(state.original_send, 'PATH_FORBIDDEN'); return
    end
    local intent = state.json.encode({method=request.method, params=params})
    if not reads[request.method] and not replay_or_admit(state, request, intent) then
      return
    end
    state.servicing_read = reads[request.method] or false
    state.original_dispatch(request)
    state.servicing_read = false
  end

  function dispatch.install(context)
    -- busy marks the single writer; active_id owns its ledger result.
    -- servicing_read prevents an interleaved read from completing that writer.
    local state = {context=context, json=context.json, runtime=context.runtime,
      ledger={}, busy=false, active_id=nil, servicing_read=false}
    unlock()
    install_inspection(state)
    register_endpoints(state)
    install_response_wrapper(state)
    state.original_dispatch = BB_DISPATCHER.dispatch
    BB_DISPATCHER.dispatch = function(request) route_request(state, request) end
  end

return dispatch
