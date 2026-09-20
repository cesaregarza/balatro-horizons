-- Horizons-only actions validate before highlights or native calls, then settle.
local action = {}
  local inventory = {buy=true, use_consumable=true, choose_pack=true, sell=true}
  local hand_action = {play_hand=true, discard=true}

  local function uses_targets(kind, args)
    return kind=='use_consumable' or kind=='choose_pack' or
      (kind=='buy' and args.mode=='buy_and_use')
  end

  local function resolve_card(args)
    local areas = {shop=G.shop_jokers, vouchers=G.shop_vouchers,
      packs=G.shop_booster, pack=G.pack_cards, hand=G.hand, jokers=G.jokers,
      consumables=G.consumeables}
    local area = areas[args.area or '']
    return area and area.cards[(args.index or -1)+1]
  end

  local function validate_targets(kind, args, card, context, send)
    if not uses_targets(kind, args) then return true end
    local hand = G.hand and G.hand.cards or {}
    local targets = args.targets or {}
    local lo, hi = context.inspect.requirements(card)
    if #targets<lo or #targets>hi then
      context.respond_error(send, 'INVALID_TARGET_COUNT'); return false
    end
    local seen = {}
    for _, index in ipairs(targets) do
      if not hand[index+1] or seen[index] then
        context.respond_error(send, 'INVALID_TARGETS'); return false
      end
      seen[index] = true
    end
    return true
  end

  local function validate_offer(kind, args, card, context, send)
    if kind=='sell' and not card:can_sell_card() then
      context.respond_error(send, 'SELL_NOT_AVAILABLE'); return false
    end
    if kind=='buy' and card.cost>0 and card.cost>G.GAME.dollars-G.GAME.bankrupt_at then
      context.respond_error(send, 'UNAFFORDABLE'); return false
    end
    if kind=='buy' and args.area=='shop' and args.mode~='buy_and_use' and
        not context.inspect.has_space(card) then
      context.respond_error(send, 'CAPACITY'); return false
    end
    if kind=='choose_pack' and card.ability.set=='Joker' and
        not G.FUNCS.check_for_buy_space(card) then
      context.respond_error(send, 'CAPACITY'); return false
    end
    return true
  end

  local function validate_consumable(kind, args, card, context, send)
    if not card.ability.consumeable or not uses_targets(kind, args) then return true end
    -- Native use_card assumes the UI checked can_use_consumeable. Restore the
    -- original highlight table even when validation rejects the selection.
    local hand = G.hand and G.hand.cards or {}
    local previous = G.hand.highlighted
    local selected = {}
    for _, index in ipairs(args.targets or {}) do table.insert(selected, hand[index+1]) end
    G.hand.highlighted = selected
    local ok, can_use = pcall(function() return card:can_use_consumeable() end)
    G.hand.highlighted = previous
    if not ok or not can_use then
      context.respond_error(send, 'USE_NOT_AVAILABLE'); return false
    end
    return true
  end

  local function apply_inventory(kind, args, card)
    if uses_targets(kind, args) then
      local hand = G.hand and G.hand.cards or {}
      G.hand:unhighlight_all()
      for _, index in ipairs(args.targets or {}) do
        G.hand:add_to_highlighted(hand[index+1], true)
      end
    end
    local event = {config={ref_table=card,
      id=args.mode=='buy_and_use' and 'buy_and_use' or nil}}
    if kind=='sell' then G.FUNCS.sell_card(event)
    elseif kind=='buy' and args.area=='shop' then G.FUNCS.buy_from_shop(event)
    else G.FUNCS.use_card(event) end
  end

  local function handle_inventory(kind, args, context, send)
    local card = resolve_card(args)
    if not card then context.respond_error(send, 'UNKNOWN_CARD'); return false end
    if not validate_targets(kind, args, card, context, send) or
        not validate_offer(kind, args, card, context, send) or
        not validate_consumable(kind, args, card, context, send) then
      return false
    end
    apply_inventory(kind, args, card)
    return true
  end

  local function handle_hand(kind, args, context, send)
    local hand = G.hand and G.hand.cards or {}
    local selected = args.cards or {}
    local seen = {}
    if #selected<1 or #selected>G.hand.config.highlighted_limit then
      context.respond_error(send, 'INVALID_CARD_COUNT'); return false
    end
    for _, index in ipairs(selected) do
      if not hand[index+1] or seen[index] then
        context.respond_error(send, 'INVALID_SELECTION'); return false
      end
      seen[index] = true
    end
    for index, hand_card in ipairs(hand) do
      if hand_card.forced_selection and not seen[index-1] then
        context.respond_error(send, 'FORCED_CARD_REQUIRED'); return false
      end
    end
    G.hand:unhighlight_all()
    for _, index in ipairs(selected) do
      if not hand[index+1].highlighted then G.hand:add_to_highlighted(hand[index+1], true) end
    end
    if kind=='play_hand' then G.FUNCS.play_cards_from_highlighted({config={}})
    else G.FUNCS.discard_cards_from_highlighted({config={}}, false) end
    return true
  end

  local function handle_other(kind, context, send)
    if kind=='skip_pack' then
      if not G.pack_cards or G.pack_cards.REMOVED then
        context.respond_error(send, 'PACK_NOT_OPEN'); return false
      end
      G.FUNCS.skip_booster({})
    elseif kind=='reroll_boss' then
      if not context.inspect.read().bh.boss_reroll_available then
        context.respond_error(send, 'REROLL_NOT_AVAILABLE'); return false
      end
      G.FUNCS.reroll_boss(nil)
    else context.respond_error(send, 'UNKNOWN_ACTION'); return false end
    return true
  end

  local function perform(args, send, context)
    if not context.inspect.ready() then
      context.respond_error(send, 'NOT_READY'); return
    end
    local kind = args.action
    local accepted
    if inventory[kind] then accepted = handle_inventory(kind, args, context, send)
    elseif hand_action[kind] then accepted = handle_hand(kind, args, context, send)
    else accepted = handle_other(kind, context, send) end
    if accepted then context.settle.defer(function() send(context.inspect.read()) end) end
  end

  function action.install(register, context)
    register('bh_action', {action={type='string',required=true}, index={type='integer'},
      area={type='string'}, targets={type='array',items='integer'},
      cards={type='array',items='integer'}, mode={type='string'}},
      function(args, send) perform(args, send, context) end)
  end

return action
