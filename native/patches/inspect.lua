-- Private game observation and the small legality predicates shared by actions.
-- No mutation or native endpoint registration belongs in this module.
local inspect = {}
  local public
  local loaded_manifest
  local busy = function() return false end
  local context = {}

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
        for _, target in ipairs(G.hand.cards) do
          if not target.edition then return true end
        end
        return false
      end
      return true -- Selection is represented by explicit public target requirements.
    end
    local ok, result = pcall(function() return card:can_use_consumeable() end)
    return ok and result or false
  end

  local function has_space(card)
    local extra = (card.edition and card.edition.negative) and 1 or 0
    if card.ability.set == 'Joker' then
      return #G.jokers.cards < G.jokers.config.card_limit + extra
    end
    if card.ability.consumeable then
      return #G.consumeables.cards < G.consumeables.config.card_limit + extra
    end
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
        for _, key in ipairs({'perish_tally','h_size','mult','chips','x_mult','h_mult',
          'h_x_mult','dollars','loyalty_remaining','t_mult','t_chips'}) do
          if card.ability and type(card.ability[key]) == 'number' then
            out.counters[key] = tostring(card.ability[key])
          end
        end
      end
    end
  end

  local function ready()
    return (G.STATE == G.STATES.GAME_OVER or G.GAME.won) or
      (G.STATE_COMPLETE and not G.CONTROLLER.locked and
        not (G.GAME.STOP_USE and G.GAME.STOP_USE > 0))
  end

  function inspect.requirements(card) return requirements(card) end
  function inspect.allowed_use(card) return allowed_use(card) end
  function inspect.has_space(card) return has_space(card) end
  function inspect.ready() return ready() end
  function inspect.set_context(values)
    public = assert(values.public)
    loaded_manifest = values.loaded_manifest
    busy = values.busy or busy
    context = values
  end

  function inspect.read()
    local state = BB_GAMESTATE.get_gamestate()
    for _, item in ipairs({{'hand', G.hand}, {'jokers', G.jokers},
      {'consumables', G.consumeables}, {'shop', G.shop_jokers},
      {'vouchers', G.shop_vouchers}, {'packs', G.shop_booster},
      {'pack', G.pack_cards}}) do
      extend_area(state[item[1]], item[2])
    end
    state.bh = {
      instance_id=os.getenv('BH_INSTANCE_ID'), loaded_manifest=loaded_manifest,
      calibration=os.getenv('BH_CALIBRATION')=='1', identity=love.filesystem.getIdentity(),
      save_directory=love.filesystem.getSaveDirectory(), game_version=G.VERSION,
      ready=ready(), busy=busy(), pack_choices=G.GAME.pack_choices,
      target=G.GAME.blind and tostring(G.GAME.blind.chips),
      profile=G.PROFILES[G.SETTINGS.profile], credit_limit=-(G.GAME.bankrupt_at or 0),
      blind_on_deck=G.GAME.blind_on_deck,
      boss_reroll_available=(G.STATE == G.STATES.BLIND_SELECT and
        (G.GAME.dollars-G.GAME.bankrupt_at)>=10 and
        (G.GAME.used_vouchers.v_retcon or
          (G.GAME.used_vouchers.v_directors_cut and
            not G.GAME.round_resets.boss_rerolled))) or false,
      profile_policy='fully_unlocked_v1', headless=BB_SETTINGS.headless,
      fast=BB_SETTINGS.fast, rng=G.GAME.pseudorandom, tags={}, deck_composition={}}
    for _, tag in ipairs(G.GAME.tags or {}) do table.insert(state.bh.tags, tag.key) end
    public.extend(state)
    state.bh.settlement = context.settlement_visible and context.settlement_visible() or nil
    for _, card in ipairs(G.playing_cards or {}) do
      local key = public.deck_key(card)
      state.bh.deck_composition[key] = (state.bh.deck_composition[key] or 0) + 1
    end
    return state
  end

return inspect
