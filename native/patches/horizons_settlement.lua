-- Observe already-computed cash-out rows. Never recalculate payouts or invoke effects.
local settlement = {}
local active = nil

local function label(config, kind)
  if kind == 'interest' then
    return localize{type='variable', key='interest', vars={G.GAME.interest_amount, 5,
      G.GAME.interest_amount*G.GAME.interest_cap/5}}
  elseif kind == 'hands' then
    return localize{type='variable', key='remaining_hand_money',
      vars={G.GAME.modifiers.money_per_hand or 1}}
  elseif kind == 'discards' then
    return localize{type='variable', key='remaining_discard_money',
      vars={G.GAME.modifiers.money_per_discard or 0}}
  elseif kind == 'blind' then
    return G.GAME.blind.name
  elseif type(config.text) == 'string' then return config.text
  elseif type(config.condition) == 'string' then return config.condition
  elseif config.card and config.card.config and config.card.config.center then
    local center = config.card.config.center
    local opts = config.loc_opts or {}
    if type(opts.text) == 'string' then return opts.text end
    return localize{type='name_text', set=opts.set or center.set, key=opts.key or center.key}
  end
  return 'Other reward'
end

local function capture(config)
  if not active or type(config) ~= 'table' then return end
  if config.name == 'bottom' then
    active.total = tostring(config.dollars or 1)
    active.complete = true
    return
  end
  active.row_count = active.row_count + 1
  -- The pinned native UI displays at most seven rows and an omitted-row count.
  if active.row_count > 7 then
    active.omitted_rows = active.omitted_rows + 1
    return
  end
  local kind = ({blind1='blind', hands='hands', discards='discards', interest='interest'})[config.name]
    or 'other'
  active.rows[#active.rows+1] = {
    kind=kind, label=label(config, kind), dollars=tostring(config.dollars or 1),
    count=config.disp and tostring(config.disp) or nil,
  }
end

function settlement.install()
  local evaluate = G.FUNCS.evaluate_round
  local add_row = add_round_eval_row
  G.FUNCS.evaluate_round = function(...)
    active = {rows={}, omitted_rows=0, row_count=0, complete=false, round=G.GAME.round}
    G.GAME.bh_public_settlement = active
    local result = evaluate(...)
    active = nil
    return result
  end
  add_round_eval_row = function(config, ...)
    capture(config)
    return add_row(config, ...)
  end
end

function settlement.visible()
  local value = G.GAME.bh_public_settlement
  if G.STATE ~= G.STATES.ROUND_EVAL or not value or not value.complete
    or value.round ~= G.GAME.round then return nil end
  return {rows=value.rows, total=value.total, omitted_rows=value.omitted_rows,
    source='native_cashout_rows'}
end

return settlement
