"""Declarative native action/expected-state rows for the shop fixture."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import TYPE_CHECKING

from balatro_horizons.contracts import RemainingBudget
from balatro_horizons.evidence.collect.action_rows import ActionRow
from balatro_horizons.evidence.collect.action_rows import buy_offer as _buy_offer
from balatro_horizons.evidence.collect.action_rows import require as _require
from balatro_horizons.evidence.collect.action_rows import reverse_ids as _reverse_ids
from balatro_horizons.evidence.collect.inventory_rows import inventory_rows as _inventory_rows
from balatro_horizons.observations.projection import HandleIssuer, project_public

if TYPE_CHECKING:
    from balatro_horizons.evidence.collect.acceptance import Audit


def run_action_table(audit: Audit, rows: list[ActionRow]) -> None:
    for row in rows:
        before = row.capture(audit)
        audit.take(row.action(audit))
        row.expected(audit, before)


def _shop_rows(audit: Audit) -> list[ActionRow]:
    skip_blind = ActionRow(
        "skip_blind",
        lambda current: {
            "type": "skip_blind",
            "blind_id": current.obs.state.revealed_blinds[0].id,
        },
    )
    rows = [
        skip_blind,
        ActionRow(
            "select_blind",
            lambda current: {
                "type": "select_blind",
                "blind_id": current.obs.state.revealed_blinds[0].id,
            },
        ),
        ActionRow(
            "reorder",
            lambda current: {
                "type": "reorder",
                "area": "hand",
                "ordered_ids": _reverse_ids(current, "hand"),
            },
            capture=lambda current: [card.id for card in current.obs.state.hand],
            expected=lambda current, before: _require(
                [card.id for card in current.obs.state.hand] == before[::-1],
                "HAND_REORDER_NOT_REVERSED",
            ),
        ),
        ActionRow(
            "discard",
            lambda current: {"type": "discard", "card_ids": [current.obs.state.hand[0].id]},
            capture=lambda current: current.obs.state.resources.discards,
            expected=lambda current, before: _require(
                current.obs.state.resources.discards == before - 1,
                "DISCARD_COUNT_NOT_DECREMENTED",
            ),
        ),
    ]
    if "skip_pack" in audit.obs.available_action_types:
        rows.insert(
            1,
            ActionRow(
                "skip_pack",
                lambda _current: {"type": "skip_pack"},
                expected=lambda current, _before: _require(
                    current.obs.state.progress.blind == "Big", "SKIP_PACK_DID_NOT_ADVANCE"
                ),
            ),
        )
    else:
        rows[0] = ActionRow(
            skip_blind.name,
            skip_blind.action,
            expected=lambda current, _before: _require(
                current.obs.state.progress.blind == "Big", "SKIP_BLIND_DID_NOT_ADVANCE"
            ),
        )
    return rows


def _round_rows() -> list[ActionRow]:
    return [
        ActionRow(
            "play_hand",
            lambda current: {
                "type": "play_hand",
                "card_ids": [current.obs.state.hand[0].id],
            },
            expected=lambda current, _before: _require(
                current.obs.phase == "ROUND_EVAL" and not current.game.terminal_status(),
                "PLAY_HAND_DID_NOT_ENTER_ROUND_EVAL",
            ),
        ),
        ActionRow(
            "cash_out",
            lambda _current: {"type": "cash_out"},
            expected=lambda current, _before: _require(
                current.obs.phase == "SHOP", "CASH_OUT_DID_NOT_RETURN_TO_SHOP"
            ),
        ),
    ]


def _pack_rows() -> list[ActionRow]:
    def choose(current):
        return {
            "type": "choose_pack",
            "offer_id": current.obs.state.offers[0].id,
            "target_ids": [],
        }
    return [
        ActionRow(
            "choose_pack",
            choose,
            expected=lambda current, _before: _require(
                current.obs.state.resources.pack_choices_remaining == 1,
                "PACK_CHOICE_COUNT_NOT_DECREMENTED",
            ),
        ),
        ActionRow(
            "choose_pack",
            choose,
            expected=lambda current, _before: _require(
                current.obs.phase == "SHOP", "PACK_SELECTION_DID_NOT_RETURN_TO_SHOP"
            ),
        ),
    ]


def _credit_rows() -> list[ActionRow]:
    return [
        ActionRow("buy", lambda current: _buy_offer(current, "pack")),
        ActionRow(
            "skip_pack",
            lambda _current: {"type": "skip_pack"},
            expected=lambda current, _before: _require(
                current.obs.phase == "SHOP", "SKIP_PACK_DID_NOT_RETURN_TO_SHOP"
            ),
        ),
        ActionRow(
            "reroll_shop",
            lambda _current: {"type": "reroll_shop"},
            expected=lambda current, _before: _require(
                float(current.obs.state.resources.money) < 0,
                "REROLL_DID_NOT_PRESERVE_NEGATIVE_MONEY",
            ),
        ),
    ]


def _use_first_consumable(audit: Audit) -> dict:
    consumable = audit.obs.state.consumables[0]
    assert consumable.usable and consumable.min_targets >= 1
    targets = [card.id for card in audit.obs.state.hand[: consumable.min_targets]]
    return {"type": "use_consumable", "consumable_id": consumable.id, "target_ids": targets}


def _consumable_before(audit: Audit) -> tuple[dict[str, str | None], set[str]]:
    consumable = audit.obs.state.consumables[0]
    targets = {card.id for card in audit.obs.state.hand[: consumable.min_targets]}
    return {card.id: card.rank for card in audit.obs.state.hand}, targets


def _assert_consumable_effect(
    audit: Audit, before: tuple[dict[str, str | None], set[str]]
) -> None:
    previous, targets = before
    assert len(audit.obs.state.consumables) == 1
    assert any(
        previous[card.id] != card.rank
        for card in audit.obs.state.hand
        if card.id in targets
    )


def _consumable_rows() -> list[ActionRow]:
    return [
        ActionRow("leave_shop", lambda _current: {"type": "leave_shop"}),
        ActionRow("reroll_boss", lambda _current: {"type": "reroll_boss"}),
        ActionRow(
            "select_blind",
            lambda current: {
                "type": "select_blind",
                "blind_id": current.obs.state.revealed_blinds[0].id,
            },
        ),
        ActionRow(
            "use_consumable",
            lambda current: _use_first_consumable(current),
            capture=_consumable_before,
            expected=_assert_consumable_effect,
        ),
    ]


def fixture(audit: Audit, case: str, check=None) -> None:
    audit.fixture(case)
    if check:
        check(audit)


def _assert_masked_projection(audit: Audit) -> None:
    assert all(
        card.face_down
        and card.sellable is None
        and not card.effects
        and not card.counters
        for card in audit.obs.state.jokers
    )
    snapshot = audit.issuer.snapshot()
    reversed_state = copy.deepcopy(audit.private_state)
    reversed_state["visible"]["jokers"].reverse()
    budget = RemainingBudget(game_actions=0, provider_calls=0)
    left = project_public(
        audit.private_state,
        episode_id=audit.eid,
        observation_id=audit.decision,
        issuer=HandleIssuer.restore(snapshot),
        memory="",
        remaining_budget=budget,
    )
    right = project_public(
        reversed_state,
        episode_id=audit.eid,
        observation_id=audit.decision,
        issuer=HandleIssuer.restore(snapshot),
        memory="",
        remaining_budget=budget,
    )
    assert left == right


def run_shop_table(audit: Audit) -> None:
    """Run the complete historical shop action table in order."""
    run_action_table(audit, _shop_rows(audit))
    fixture(
        audit,
        "easy_blind",
        lambda current: _require(
            current.obs.state.resources.target == "1", "EASY_BLIND_TARGET_MISMATCH"
        ),
    )
    run_action_table(audit, _round_rows()[:1])
    audit.capture()
    run_action_table(audit, _round_rows()[1:])
    fixture(
        audit,
        "shop",
        lambda current: _require(
            len(current.obs.state.jokers) == 5
            and len(current.obs.state.consumables) == 2,
            "SHOP_INVENTORY_MISMATCH",
        ),
    )
    run_action_table(audit, _inventory_rows())
    audit.capture()
    run_action_table(audit, _pack_rows())
    fixture(audit, "another_pack")
    credit_rows = _credit_rows()
    run_action_table(audit, credit_rows[:2])
    # Establish debt before rerolling; this proves credit permits a negative balance.
    fixture(
        audit,
        "credit",
        lambda current: _require(
            current.obs.state.resources.money == "-5"
            and current.obs.state.resources.credit_limit == "20",
            "CREDIT_FIXTURE_MISMATCH",
        ),
    )
    run_action_table(audit, credit_rows[2:])
    audit.capture()
    run_action_table(audit, _consumable_rows())
    audit.capture()
    fixture(audit, "mask_jokers", _assert_masked_projection)


def shop_action_types(*, tag_pack_opened: bool = True) -> list[str | dict[str, str]]:
    """Return action and fixture order for the non-executing shop golden."""
    available = ["skip_pack"] if tag_pack_opened else []
    audit = SimpleNamespace(obs=SimpleNamespace(available_action_types=available))

    def names(rows):
        return [row.name for row in rows]

    credit_rows = _credit_rows()
    return [
        *names(_shop_rows(audit)),
        {"fixture": "easy_blind"},
        *names(_round_rows()),
        {"fixture": "shop"},
        *names(_inventory_rows()),
        *names(_pack_rows()),
        {"fixture": "another_pack"},
        *names(credit_rows[:2]),
        {"fixture": "credit"},
        *names(credit_rows[2:]),
        *names(_consumable_rows()),
        {"fixture": "mask_jokers"},
    ]


def win_rows() -> list[ActionRow]:
    return [
        ActionRow(
            "select_blind",
            lambda current: {
                "type": "select_blind",
                "blind_id": current.obs.state.revealed_blinds[0].id,
            },
        ),
        ActionRow(
            "play_hand",
            lambda current: {"type": "play_hand", "card_ids": [current.obs.state.hand[0].id]},
            expected=lambda current, _before: _require(
                current.game.terminal_status() == "WIN", "WIN_FIXTURE_DID_NOT_WIN"
            ),
        ),
    ]
