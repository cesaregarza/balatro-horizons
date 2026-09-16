-- Project-owned evaluator instrumentation for the pinned BalatroBot bridge.
-- No endpoint in this file is exposed directly to playing providers.
local json = require('json')
local public = assert(SMODS.load_file('horizons_public.lua'))()
local settlement = assert(SMODS.load_file('horizons_settlement.lua'))()
settlement.install()
local token = assert(os.getenv('BH_TOKEN'), 'Missing private bridge token')
local runtime = assert(os.getenv('BH_RUNTIME'), 'Missing isolated runtime directory')
local manifest_file=assert(io.open(runtime .. '/environment.lock.json','r'))
local loaded_manifest=json.decode(manifest_file:read('*a')); manifest_file:close()
assert(BH_ISOLATION and love.filesystem.getIdentity() == 'BalatroHorizons', 'Save isolation not established')
-- Fresh isolated profiles have no tutorial progress; mark tutorial completion before main_menu.
G.SETTINGS.tutorial_complete = true
G.SETTINGS.tutorial_progress = nil
G.SETTINGS.crashreports = false
G.F_CRASH_REPORTS = false
local ledger = {}
local busy = false
local pending = nil
local function persist_ledger()
  local f = assert(io.open(runtime .. '/request-ledger.json', 'w'))
  f:write(json.encode(ledger)); f:flush(); f:close()
end
local function unlock()
  for _, pool in ipairs({G.P_CENTERS, G.P_BLINDS, G.P_TAGS}) do
    for _, center in pairs(pool or {}) do center.unlocked = true; center.discovered = true end
  end
end
unlock()
local function ready()
  return (G.STATE == G.STATES.GAME_OVER or G.GAME.won) or
    (G.STATE_COMPLETE and not G.CONTROLLER.locked and not (G.GAME.STOP_USE and G.GAME.STOP_USE > 0))
end
local function requirements(card)
  local config = (card.config and card.config.center and card.config.center.config) or {}
  local key = card.config and card.config.center and card.config.center.key
  if key == 'c_aura' then return 1, 1 end
  if config.max_highlighted then return config.min_highlighted or 1, config.max_highlighted end
  return 0, 0
end
local function allowed_use(card)
  if not card.ability.consumeable then return false end
  local lo, hi = requirements(card)
  if lo > 0 and G.hand and #G.hand.cards >= lo then
    local key = card.config.center.key
    if key == 'c_aura' then
      for _, target in ipairs(G.hand.cards) do if not target.edition then return true end end
      return false
    end
    return true -- Selection is represented by explicit public target requirements.
  end
  local ok, result = pcall(function() return card:can_use_consumeable() end)
  return ok and result or false
end
local function has_space(card)
  local extra = (card.edition and card.edition.negative) and 1 or 0
  if card.ability.set == 'Joker' then return #G.jokers.cards < G.jokers.config.card_limit + extra end
  if card.ability.consumeable then return #G.consumeables.cards < G.consumeables.config.card_limit + extra end
  return true
end
local function extend_area(raw, area)
  if not raw or not area then return end
  for i, card in ipairs(area.cards or {}) do
    local out = raw.cards[i]
    if out then
      public.card_label(card, out)
      out.min_targets, out.max_targets = requirements(card)
      out.usable = allowed_use(card)
      out.sellable = card:can_sell_card()
      out.acquire_allowed = has_space(card)
      out.forced_selection = card.forced_selection or false
      out.counters = {}
      for _, key in ipairs({'perish_tally','h_size','mult','chips','x_mult','h_mult','h_x_mult','dollars','loyalty_remaining','t_mult','t_chips'}) do
        if card.ability and type(card.ability[key]) == 'number' then out.counters[key] = tostring(card.ability[key]) end
      end
    end
  end
end
local function inspect()
  local state = BB_GAMESTATE.get_gamestate()
  for _, item in ipairs({{'hand',G.hand},{'jokers',G.jokers},{'consumables',G.consumeables},
    {'shop',G.shop_jokers},{'vouchers',G.shop_vouchers},{'packs',G.shop_booster},{'pack',G.pack_cards}}) do
    extend_area(state[item[1]],item[2])
  end
  state.bh = {instance_id=os.getenv('BH_INSTANCE_ID'),loaded_manifest=loaded_manifest,calibration=os.getenv('BH_CALIBRATION')=='1',identity=love.filesystem.getIdentity(),save_directory=love.filesystem.getSaveDirectory(),
    game_version=G.VERSION,ready=ready(),busy=busy,pack_choices=G.GAME.pack_choices,
    target=G.GAME.blind and tostring(G.GAME.blind.chips),profile=G.PROFILES[G.SETTINGS.profile],
    credit_limit=-(G.GAME.bankrupt_at or 0),blind_on_deck=G.GAME.blind_on_deck,
    boss_reroll_available=(G.STATE == G.STATES.BLIND_SELECT and (G.GAME.dollars-G.GAME.bankrupt_at)>=10 and
      (G.GAME.used_vouchers.v_retcon or (G.GAME.used_vouchers.v_directors_cut and not G.GAME.round_resets.boss_rerolled))) or false,
    profile_policy='fully_unlocked_v1',headless=BB_SETTINGS.headless,fast=BB_SETTINGS.fast,
    rng=G.GAME.pseudorandom, tags={},deck_composition={}}
  for _, tag in ipairs(G.GAME.tags or {}) do table.insert(state.bh.tags,tag.key) end
  public.extend(state)
  state.bh.settlement = settlement.visible()
  for _, card in ipairs(G.playing_cards or {}) do
    local key = public.deck_key(card)
    state.bh.deck_composition[key]=(state.bh.deck_composition[key] or 0)+1
  end
  return state
end
local function respond_error(send, code)
  send({message=code,name=BB_ERROR_NAMES.NOT_ALLOWED})
end
local function register(name, schema, execute)
  assert(BB_DISPATCHER.register({name=name,description='Horizons private evaluator',schema=schema,execute=execute}))
end
register('bh_inspect', {}, function(_, send) send(inspect()) end)
assert(SMODS.load_file('horizons_fixtures.lua'))()(register,inspect)
register('bh_request_status', {request_id={type='string',required=true}}, function(args,send)
  send(ledger[args.request_id] or {status='unknown'})
end)
register('bh_rules', {}, function(_,send)
  local rules = {}
  for set, items in pairs(G.localization.descriptions or {}) do
    for key, value in pairs(items) do
      rules[key]={set=set,name=value.name,text=value.text}
    end
  end
  send({rules=rules})
end)
-- Custom actions cover gaps in upstream's schemas and capacity checks.
register('bh_action', {action={type='string',required=true}, index={type='integer'},
  area={type='string'}, targets={type='array',items='integer'}, cards={type='array',items='integer'},
  mode={type='string'}}, function(args,send)
  if not ready() then respond_error(send,'NOT_READY'); return end
  local a = args.action
  local areas = {shop=G.shop_jokers,vouchers=G.shop_vouchers,packs=G.shop_booster,pack=G.pack_cards,
    hand=G.hand,jokers=G.jokers,consumables=G.consumeables}
  local area=areas[args.area or '']
  local card=area and area.cards[(args.index or -1)+1]
  local hand=G.hand and G.hand.cards or {}
  local targets=args.targets or {}
  if a=='buy' or a=='use_consumable' or a=='choose_pack' or a=='sell' then
    if not card then respond_error(send,'UNKNOWN_CARD'); return end
    local lo,hi=requirements(card)
    if (a=='use_consumable' or (a=='buy' and args.mode=='buy_and_use') or a=='choose_pack') then
      if #targets<lo or #targets>hi then respond_error(send,'INVALID_TARGET_COUNT'); return end
      local seen={}
      for _,i in ipairs(targets) do
        if not hand[i+1] or seen[i] then respond_error(send,'INVALID_TARGETS'); return end
        seen[i]=true
      end
    end
    if a=='sell' and not card:can_sell_card() then respond_error(send,'SELL_NOT_AVAILABLE'); return end
    if a=='buy' and card.cost>0 and card.cost>G.GAME.dollars-G.GAME.bankrupt_at then respond_error(send,'UNAFFORDABLE'); return end
    if a=='buy' and args.area=='shop' and args.mode~='buy_and_use' and not has_space(card) then respond_error(send,'CAPACITY'); return end
    if a=='choose_pack' and card.ability.set=='Joker' and not G.FUNCS.check_for_buy_space(card) then respond_error(send,'CAPACITY'); return end
    if card.ability.consumeable and (a=='use_consumable' or a=='choose_pack' or (a=='buy' and args.mode=='buy_and_use')) then
      -- Native use_card assumes the UI already checked can_use_consumeable.
      -- Validate the explicit selection without changing highlights or queuing effects.
      local previous=G.hand.highlighted
      local selected={}
      for _,i in ipairs(targets) do table.insert(selected,hand[i+1]) end
      G.hand.highlighted=selected
      local ok,can_use=pcall(function() return card:can_use_consumeable() end)
      G.hand.highlighted=previous
      if not ok or not can_use then respond_error(send,'USE_NOT_AVAILABLE'); return end
    end
    if a=='use_consumable' or a=='choose_pack' or (a=='buy' and args.mode=='buy_and_use') then
      G.hand:unhighlight_all()
      for _,i in ipairs(targets) do G.hand:add_to_highlighted(hand[i+1],true) end
    end
    local e={config={ref_table=card, id=args.mode=='buy_and_use' and 'buy_and_use' or nil}}
    if a=='sell' then G.FUNCS.sell_card(e)
    elseif a=='buy' and args.area=='shop' then G.FUNCS.buy_from_shop(e)
    else G.FUNCS.use_card(e) end
  elseif a=='play_hand' or a=='discard' then
    local selected=args.cards or {}
    local seen={}
    if #selected<1 or #selected>G.hand.config.highlighted_limit then respond_error(send,'INVALID_CARD_COUNT'); return end
    for _,i in ipairs(selected) do
      if not hand[i+1] or seen[i] then respond_error(send,'INVALID_SELECTION'); return end
      seen[i]=true
    end
    for i,c in ipairs(hand) do
      if c.forced_selection and not seen[i-1] then respond_error(send,'FORCED_CARD_REQUIRED'); return end
    end
    G.hand:unhighlight_all()
    for _,i in ipairs(selected) do if not hand[i+1].highlighted then G.hand:add_to_highlighted(hand[i+1],true) end end
    if a=='play_hand' then G.FUNCS.play_cards_from_highlighted({config={}})
    else G.FUNCS.discard_cards_from_highlighted({config={}},false) end
  elseif a=='skip_pack' then
    if not G.pack_cards or G.pack_cards.REMOVED then respond_error(send,'PACK_NOT_OPEN'); return end
    G.FUNCS.skip_booster({})
  elseif a=='reroll_boss' then
    if not inspect().bh.boss_reroll_available then respond_error(send,'REROLL_NOT_AVAILABLE'); return end
    G.FUNCS.reroll_boss(nil)
  else respond_error(send,'UNKNOWN_ACTION'); return end
  -- Wait across the transition, then require several consecutive settled frames.
  pending={send=send,age=0,stable=0}
end)
local original_update=love.update
love.update=function(dt)
  original_update(dt)
  if pending then
    pending.age=pending.age+1
    if pending.age>30 and ready() then pending.stable=pending.stable+1 else pending.stable=0 end
    if pending.stable>=10 then local callback=pending.send; pending=nil; callback(inspect()) end
  end
end
-- Authenticate before upstream logging or validation; redact raw errors at Python boundary.
local original_dispatch=BB_DISPATCHER.dispatch
local original_send=BB_SERVER.send_response
local active_id=nil
local servicing_read=false
BB_SERVER.send_response=function(response)
  if active_id and not servicing_read then
    ledger[active_id]={status=response.message and 'rejected' or 'committed', response=response, intent=ledger[active_id].intent}
    persist_ledger(); active_id=nil; busy=false
  end
  return original_send(response)
end
local reads={health=true,bh_inspect=true,bh_request_status=true,bh_rules=true}
local allowed={health=true,bh_inspect=true,bh_request_status=true,bh_rules=true,bh_action=true,
 bh_fixture=true,start=true,menu=true,save=true,load=true,select=true,skip=true,cash_out=true,next_round=true,
 reroll=true,rearrange=true,pack=true}
BB_DISPATCHER.dispatch=function(request)
  local params=request.params or {}
  if params._bh_token~=token then respond_error(original_send,'UNAUTHORIZED'); return end
  params._bh_token=nil; request.params=params
  if not allowed[request.method] then respond_error(original_send,'METHOD_FORBIDDEN'); return end
  if (request.method=='save' or request.method=='load') and
     (type(params.path)~='string' or not params.path:match('^D:/BalatroHorizonsRuntime/checkpoints/[a-f0-9]+%.jkr$')) then
    respond_error(original_send,'PATH_FORBIDDEN'); return
  end
  local intent=json.encode({method=request.method,params=params})
  if not reads[request.method] then
    if ledger[tostring(request.id)] then
      local record=ledger[tostring(request.id)]
      if record.intent~=intent then respond_error(original_send,'REQUEST_ID_REUSED'); return end
      if record.response then original_send(record.response) else respond_error(original_send,'ACTION_STATUS_UNKNOWN') end
      return
    end
    if busy then respond_error(original_send,'BUSY'); return end
    unlock()
    busy=true; active_id=tostring(request.id)
    ledger[active_id]={status='pending',intent=intent}; persist_ledger()
  end
  servicing_read=reads[request.method] or false
  original_dispatch(request)
  servicing_read=false
end
