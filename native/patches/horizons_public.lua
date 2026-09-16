-- Read native-visible descriptions without constructing cards/tags or firing effects.
-- Steamodded's pinned Tag:get_uibox_table(..., true) returns only localization vars.
local public = {}

local function text_nodes(node, result)
  if type(node) ~= 'table' then return end
  if node.config and type(node.config.text) == 'string' then
    result[#result+1] = node.config.text
  end
  for _, child in ipairs(node) do text_nodes(child, result) end
  if node.nodes then text_nodes(node.nodes, result) end
  if node.children then text_nodes(node.children, result) end
end

local function cleanup(node, seen)
  if type(node) ~= 'table' or seen[node] then return end
  seen[node] = true
  local object = node.config and node.config.object
  if object and object.remove then object:remove(); node.config.object = nil end
  for key, child in pairs(node) do
    if key ~= 'config' and type(child) == 'table' then cleanup(child, seen) end
  end
end

local function description(center, vars, object)
  if not center then return {label='Unknown',effects={}} end
  -- This is the same native UI description builder used by the card tooltip.
  -- Read only its main text; auxiliary UI nodes are cleaned up, never serialized.
  local ui = generate_card_ui(center, nil, vars, center.set, nil, nil, nil, nil, object)
  local words = {}
  text_nodes(ui and ui.main, words)
  cleanup(ui, {})
  return {
    label=localize({type='name_text',set=center.set,key=center.key}),
    effects=#words > 0 and {table.concat(words,' ')} or {},
  }
end

local function base_visibility(card)
  local replaces_base = SMODS.has_playing_card_property(card, 'replace_base_card')
  return not (replaces_base or SMODS.has_no_rank(card)),
    not (replaces_base or SMODS.has_no_suit(card))
end

function public.deck_key(card)
  local rank_visible, suit_visible = base_visibility(card)
  return (suit_visible and tostring(card.base and card.base.suit) or 'No suit')..':'..
    (rank_visible and tostring(card.base and card.base.value) or 'No rank')
end

function public.card_label(card, out)
  if out.value and (out.value.rank or out.value.suit) then
    out.rank_visible, out.suit_visible = base_visibility(card)
    if not out.rank_visible then out.value.rank = nil end
    if not out.suit_visible then out.value.suit = nil end
  end
  if out.value and out.value.rank and out.value.suit then
    -- Python formats the already-observed rank/suit; don't overwrite it with Base Card.
    out.label = nil
  else
    out.label = card.ability and card.ability.name or out.label
  end
end

function public.tag_description(tag)
  if tag.hide_ability then return {label='Hidden tag',effects={}} end
  local center = G.P_TAGS and G.P_TAGS[tag.key]
  if not center then return {label='Unknown tag',effects={}} end
  return description(center, Tag.get_uibox_table(tag, nil, true), tag)
end

function public.extend(state)
  state.bh.blind_disabled = G.GAME.blind and G.GAME.blind.disabled or false
  state.bh.owned_vouchers = {}
  local keys = {}
  for key, owned in pairs(G.GAME.used_vouchers or {}) do
    if owned then keys[#keys+1] = key end
  end
  table.sort(keys)
  for _, key in ipairs(keys) do
    state.bh.owned_vouchers[#state.bh.owned_vouchers+1] = description(G.P_CENTERS[key])
  end
  state.bh.pending_tags = {}
  for _, tag in ipairs(G.GAME.tags or {}) do
    if not tag.triggered then
      state.bh.pending_tags[#state.bh.pending_tags+1] = public.tag_description(tag)
    end
  end
  local resets = G.GAME.round_resets or {}
  for _, item in ipairs({{'small','Small'},{'big','Big'}}) do
    local blind = (state.blinds or {})[item[1]]
    local key = (resets.blind_tags or {})[item[2]]
    local center = key and G.P_TAGS[key]
    if blind and center then
      -- A plain description proxy avoids Tag() and its RNG/registration effects.
      local orbital = ((G.GAME.orbital_choices or {})[resets.ante] or {})[item[2]]
      local tag = {key=key,name=center.name,config=center.config,
        ability={orbital_hand=orbital or '['..localize('k_poker_hand')..']'}}
      local reward = public.tag_description(tag)
      blind.tag_name = reward.label
      blind.tag_effect = table.concat(reward.effects,' ')
    end
  end
end

return public
