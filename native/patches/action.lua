-- Horizons-only actions. Validation happens before any highlight or native call;
-- successful actions are parked on the shared settlement queue.
local function module()
  local action = {}

  function action.install(register, context)
    local inspect = context.inspect
    local settle = context.settle
    local respond_error = context.respond_error
    register('bh_action', {action={type='string',required=true}, index={type='integer'},
      area={type='string'}, targets={type='array',items='integer'},
      cards={type='array',items='integer'}, mode={type='string'}}, function(args, send)
      if not inspect.ready() then respond_error(send, 'NOT_READY'); return end
      local kind = args.action
      local areas = {shop=G.shop_jokers, vouchers=G.shop_vouchers,
        packs=G.shop_booster, pack=G.pack_cards, hand=G.hand, jokers=G.jokers,
        consumables=G.consumeables}
      local area = areas[args.area or '']
      local card = area and area.cards[(args.index or -1)+1]
      local hand = G.hand and G.hand.cards or {}
      local targets = args.targets or {}
      if kind=='buy' or kind=='use_consumable' or kind=='choose_pack' or kind=='sell' then
        if not card then respond_error(send, 'UNKNOWN_CARD'); return end
        local lo, hi = inspect.requirements(card)
        if (kind=='use_consumable' or (kind=='buy' and args.mode=='buy_and_use') or
            kind=='choose_pack') then
          if #targets<lo or #targets>hi then
            respond_error(send, 'INVALID_TARGET_COUNT'); return
          end
          local seen = {}
          for _, index in ipairs(targets) do
            if not hand[index+1] or seen[index] then
              respond_error(send, 'INVALID_TARGETS'); return
            end
            seen[index] = true
          end
        end
        if kind=='sell' and not card:can_sell_card() then
          respond_error(send, 'SELL_NOT_AVAILABLE'); return
        end
        if kind=='buy' and card.cost>0 and card.cost>G.GAME.dollars-G.GAME.bankrupt_at then
          respond_error(send, 'UNAFFORDABLE'); return
        end
        if kind=='buy' and args.area=='shop' and args.mode~='buy_and_use' and
            not inspect.has_space(card) then
          respond_error(send, 'CAPACITY'); return
        end
        if kind=='choose_pack' and card.ability.set=='Joker' and
            not G.FUNCS.check_for_buy_space(card) then
          respond_error(send, 'CAPACITY'); return
        end
        if card.ability.consumeable and
            (kind=='use_consumable' or kind=='choose_pack' or
              (kind=='buy' and args.mode=='buy_and_use')) then
          -- Native use_card assumes the UI checked can_use_consumeable. Validate
          -- explicit selection without changing highlights or queuing effects.
          local previous = G.hand.highlighted
          local selected = {}
          for _, index in ipairs(targets) do table.insert(selected, hand[index+1]) end
          G.hand.highlighted = selected
          local ok, can_use = pcall(function() return card:can_use_consumeable() end)
          G.hand.highlighted = previous
          if not ok or not can_use then
            respond_error(send, 'USE_NOT_AVAILABLE'); return
          end
        end
        if kind=='use_consumable' or kind=='choose_pack' or
            (kind=='buy' and args.mode=='buy_and_use') then
          G.hand:unhighlight_all()
          for _, index in ipairs(targets) do G.hand:add_to_highlighted(hand[index+1], true) end
        end
        local event = {config={ref_table=card,
          id=args.mode=='buy_and_use' and 'buy_and_use' or nil}}
        if kind=='sell' then G.FUNCS.sell_card(event)
        elseif kind=='buy' and args.area=='shop' then G.FUNCS.buy_from_shop(event)
        else G.FUNCS.use_card(event) end
      elseif kind=='play_hand' or kind=='discard' then
        local selected = args.cards or {}
        local seen = {}
        if #selected<1 or #selected>G.hand.config.highlighted_limit then
          respond_error(send, 'INVALID_CARD_COUNT'); return
        end
        for _, index in ipairs(selected) do
          if not hand[index+1] or seen[index] then
            respond_error(send, 'INVALID_SELECTION'); return
          end
          seen[index] = true
        end
        for index, hand_card in ipairs(hand) do
          if hand_card.forced_selection and not seen[index-1] then
            respond_error(send, 'FORCED_CARD_REQUIRED'); return
          end
        end
        G.hand:unhighlight_all()
        for _, index in ipairs(selected) do
          if not hand[index+1].highlighted then G.hand:add_to_highlighted(hand[index+1], true) end
        end
        if kind=='play_hand' then G.FUNCS.play_cards_from_highlighted({config={}})
        else G.FUNCS.discard_cards_from_highlighted({config={}}, false) end
      elseif kind=='skip_pack' then
        if not G.pack_cards or G.pack_cards.REMOVED then
          respond_error(send, 'PACK_NOT_OPEN'); return
        end
        G.FUNCS.skip_booster({})
      elseif kind=='reroll_boss' then
        local state = inspect.read()
        if not state.bh.boss_reroll_available then
          respond_error(send, 'REROLL_NOT_AVAILABLE'); return
        end
        G.FUNCS.reroll_boss(nil)
      else
        respond_error(send, 'UNKNOWN_ACTION'); return
      end
      settle.defer(function() send(inspect.read()) end)
    end)
  end

  return action
end

return module
