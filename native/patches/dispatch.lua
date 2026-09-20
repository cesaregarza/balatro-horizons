-- Private dispatcher admission, request ledger, and upstream bridge wrapping.
-- busy remains true while a mutator is settling; active_id identifies the one
-- writer whose eventual response owns that ledger entry. servicing_read keeps
-- read responses from accidentally completing or clearing that writer.
local function module()
  local dispatch = {}

  function dispatch.install(context)
    local json = context.json
    local inspect = context.inspect
    local settle = context.settle
    local reset = context.reset
    local token = context.token
    local runtime = context.runtime
    local public = context.public
    local ledger = {}
    local busy = false
    local active_id
    local servicing_read = false

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

    local function error_name(code)
      local kind = action_codes[code] and 'NOT_ALLOWED' or
        infrastructure_codes[code] and 'INFRASTRUCTURE' or
        harness_codes[code] and 'HARNESS_FAULT' or nil
      if not kind then kind = 'INFRASTRUCTURE' end
      return (BB_ERROR_NAMES and BB_ERROR_NAMES[kind]) or kind
    end

    local function respond_error(send, code)
      send({message=code, name=error_name(code)})
    end

    local function persist_ledger()
      local file = assert(io.open(runtime .. '/request-ledger.json', 'w'))
      file:write(json.encode(ledger)); file:flush(); file:close()
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

    unlock()

    local function complete(response, original_send)
      if active_id then
        ledger[active_id] = {
          status=response.message and 'rejected' or 'committed', response=response,
          intent=ledger[active_id].intent,
        }
        persist_ledger()
        active_id = nil
        busy = false
      end
      return original_send(response)
    end

    inspect.set_context({
      public=public, loaded_manifest=context.loaded_manifest,
      busy=function() return busy end,
      settlement_visible=function() return context.native_settlement_visible and
        context.native_settlement_visible() or nil end,
    })
    settle.install(inspect.ready)

    register('bh_inspect', {}, function(_, send) send(inspect.read()) end)
    assert(SMODS.load_file('horizons_fixtures.lua'))()(register, inspect.read)
    register('bh_request_status', {request_id={type='string',required=true}}, function(args, send)
      send(ledger[args.request_id] or {status='unknown'})
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
    context.action.install(register, {inspect=inspect, settle=settle,
      respond_error=respond_error})

    local reads = {health=true, bh_inspect=true, bh_request_status=true, bh_rules=true}
    -- Keep this list synchronized with the Python and PowerShell bridges.
    local allowed = {
      health=true, bh_inspect=true, bh_request_status=true, bh_rules=true,
      bh_action=true, bh_fixture=true, start=true, menu=true, save=true,
      load=true, select=true, skip=true, cash_out=true, next_round=true,
      reroll=true, rearrange=true, pack=true,
    }

    local function checkpoint_path_ok(path)
      if type(path) ~= 'string' then return false end
      local root = runtime:gsub('\\', '/'):gsub('/+$', '')
      local prefix = root .. '/checkpoints/'
      if path:sub(1, #prefix) ~= prefix then return false end
      return path:sub(#prefix + 1):match('^[a-f0-9]+%.jkr$') ~= nil
    end

    local original_send = BB_SERVER.send_response
    local settling_response = false
    BB_SERVER.send_response = function(response)
      if active_id and not servicing_read then
        if response.message or settling_response or settle.running() then
          return complete(response, original_send)
        end
        -- Upstream mutators are not returned at dispatch time: hold their
        -- accepted response on the same pending queue as bh_action.
        settle.defer(function()
          settling_response = true
          complete(response, original_send)
          settling_response = false
        end)
        return response
      end
      return original_send(response)
    end

    local original_dispatch = BB_DISPATCHER.dispatch
    BB_DISPATCHER.dispatch = function(request)
      local params = request.params or {}
      if params._bh_token ~= token then
        respond_error(original_send, 'UNAUTHORIZED'); return
      end
      params._bh_token = nil
      request.params = params
      if not allowed[request.method] then
        respond_error(original_send, 'METHOD_FORBIDDEN'); return
      end
      if (request.method=='save' or request.method=='load') and
          not checkpoint_path_ok(params.path) then
        respond_error(original_send, 'PATH_FORBIDDEN'); return
      end
      local intent = json.encode({method=request.method, params=params})
      if not reads[request.method] then
        if ledger[tostring(request.id)] then
          local record = ledger[tostring(request.id)]
          if record.intent ~= intent then
            respond_error(original_send, 'REQUEST_ID_REUSED'); return
          end
          -- Replays intentionally bypass busy and settlement. A committed
          -- response is immutable; a pending one reports infrastructure state.
          if record.response then original_send(record.response)
          else respond_error(original_send, 'ACTION_STATUS_UNKNOWN') end
          return
        end
        if busy then respond_error(original_send, 'BUSY'); return end
        if request.method == 'start' and G.STATE == G.STATES.MENU then
          reset.before_start()
        end
        unlock()
        busy = true
        active_id = tostring(request.id)
        ledger[active_id] = {status='pending', intent=intent}
        persist_ledger()
      end
      servicing_read = reads[request.method] or false
      original_dispatch(request)
      servicing_read = false
    end
  end

  return dispatch
end

return module
