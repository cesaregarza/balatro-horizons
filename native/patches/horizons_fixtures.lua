-- Named evaluator-only native fixtures. These episodes cannot enter autonomous scores.
return function(register,inspect)
  local function add(set,area,key,owned,negative)
    local card=create_card(set,area,nil,nil,nil,nil,key,'bh_fixture')
    if negative then card:set_edition({negative=true},true,true) end
    if owned then card:add_to_deck() end
    area:emplace(card)
    if not owned then card:set_cost() end
    return card
  end
  local function clear(area)
    for i=#area.cards,1,-1 do area.cards[i]:remove() end
  end
  register('bh_fixture',{case={type='string',required=true}},function(args,send)
    assert(os.getenv('BH_CALIBRATION')=='1','Fixtures require evaluator calibration mode')
    if args.case=='easy_blind' then
      assert(G.STATE==G.STATES.SELECTING_HAND,'Expected active blind')
      G.GAME.blind.chips=1; G.GAME.blind.chip_text='1'; G.GAME.dollars=100
    elseif args.case=='reorder_inventory' then
      assert(G.STATE==G.STATES.BLIND_SELECT,'Expected blind selection')
      clear(G.jokers); clear(G.consumeables)
      add('Joker',G.jokers,'j_dusk',true)
      add('Joker',G.jokers,'j_green_joker',true,true)
      add('Joker',G.jokers,'j_half',true)
      add('Joker',G.jokers,'j_supernova',true)
      add('Joker',G.jokers,'j_order',true):set_edition({polychrome=true},true,true)
      add('Joker',G.jokers,'j_abstract',true)
      add('Tarot',G.consumeables,'c_strength',true)
      add('Tarot',G.consumeables,'c_hermit',true)
    elseif args.case=='shop' then
      assert(G.STATE==G.STATES.SHOP,'Expected shop')
      G.GAME.dollars=200
      clear(G.shop_jokers); clear(G.shop_vouchers); clear(G.shop_booster)
      add('Joker',G.shop_jokers,'j_joker',false,true)
      add('Tarot',G.shop_jokers,'c_hermit',false)
      add('Voucher',G.shop_vouchers,'v_directors_cut',false)
      add('Booster',G.shop_booster,'p_buffoon_mega_1',false)
      while #G.jokers.cards<5 do add('Joker',G.jokers,'j_joker',true) end
      while #G.consumeables.cards<2 do add('Tarot',G.consumeables,'c_strength',true) end
    elseif args.case=='another_pack' then
      assert(G.STATE==G.STATES.SHOP,'Expected shop')
      add('Booster',G.shop_booster,'p_buffoon_mega_1',false)
    elseif args.case=='credit' then
      assert(G.STATE==G.STATES.SHOP,'Expected shop')
      add('Joker',G.jokers,'j_credit_card',true,true)
      G.GAME.dollars=-5
    elseif args.case=='invalid_consumables' then
      assert(G.STATE==G.STATES.SELECTING_HAND,'Expected active hand')
      clear(G.jokers); clear(G.consumeables)
      add('Spectral',G.consumeables,'c_ankh',true)
      add('Spectral',G.consumeables,'c_aura',true)
      for i,card in ipairs(G.hand.cards) do
        if i>1 then card:set_edition({foil=true},true,true) end
      end
    elseif args.case=='mask_jokers' then
      for _,card in ipairs(G.jokers.cards) do card.facing='back' end
    elseif args.case=='shuffle_masked_jokers' then
      local cards={}
      for i=#G.jokers.cards,1,-1 do cards[#cards+1]=G.jokers.cards[i] end
      G.jokers.cards=cards
    elseif args.case=='win_setup' then
      assert(G.STATE==G.STATES.BLIND_SELECT,'Expected blind selection')
      G.GAME.round_resets.ante=8; G.GAME.win_ante=8
      G.GAME.blind_on_deck='Boss'
      G.GAME.round_resets.blind_choices.Boss='bl_hook'
      G.GAME.round_resets.blind_states.Small='Defeated'
      G.GAME.round_resets.blind_states.Big='Defeated'
      G.GAME.round_resets.blind_states.Boss='Select'
      G.STATE_COMPLETE=false
    else error('Unknown evaluator fixture') end
    send(inspect())
  end)
end
