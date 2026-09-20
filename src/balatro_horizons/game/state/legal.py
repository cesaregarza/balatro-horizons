"""Native phase and inventory rules for available public actions."""

from .cards import AREAS, HAND_REORDER_PHASES, REORDER_PHASES

PACK_PHASES = frozenset(
    {
        "SMODS_BOOSTER_OPENED",
        "TAROT_PACK",
        "SPECTRAL_PACK",
        "PLANET_PACK",
        "STANDARD_PACK",
        "BUFFOON_PACK",
    }
)

# Keep this table as the source of the ordinary phase actions. Conditional
# blind actions are added by actions_for_phase to retain native ordering.
PHASE_ACTIONS = {
    "SELECTING_HAND": ("play_hand",),
    "ROUND_EVAL": ("cash_out",),
    "SHOP": ("buy", "reroll_shop", "leave_shop"),
    **{phase: ("choose_pack", "skip_pack") for phase in PACK_PHASES},
}


def is_pack_phase(phase):
    """Whether a phase presents pack offers rather than shop offers."""
    return phase in PACK_PHASES


def actions_for_phase(phase, blind, round_state):
    """Return actions intrinsic to a phase, in the native order."""
    if phase == "BLIND_SELECT":
        actions = ["select_blind"]
        if (blind.get("blind_on_deck") or "").lower() != "boss":
            actions.append("skip_blind")
        if blind.get("boss_reroll_available"):
            actions.append("reroll_boss")
        return actions
    actions = list(PHASE_ACTIONS.get(phase, ()))
    if phase == "SELECTING_HAND" and (round_state.get("discards_left") or 0) > 0:
        actions.append("discard")
    return actions


def reorder_areas(phase, visible):
    """Return card areas that can be reordered at this decision boundary."""
    return [
        area
        for area in AREAS
        if len(visible[area]) > 1
        and phase in (HAND_REORDER_PHASES if area == "hand" else REORDER_PHASES)
    ]


def add_inventory_actions(actions, phase, won, visible):
    """Append sell/use/reorder actions and return the reorderable areas."""
    if phase in ("MENU", "GAME_OVER") or won:
        return []
    if any(
        card["sellable"] or card["face_down"]
        for card in visible["jokers"] + visible["consumables"]
    ):
        actions.append("sell")
    if any(card["usable"] for card in visible["consumables"]):
        actions.append("use_consumable")
    areas = reorder_areas(phase, visible)
    if areas:
        actions.append("reorder")
    return areas
