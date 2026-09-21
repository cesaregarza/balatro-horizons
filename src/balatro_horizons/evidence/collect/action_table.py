"""Declarative native action/expected-state rows for the shop fixture."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from balatro_horizons.contracts import RemainingBudget
from balatro_horizons.observations.projection import HandleIssuer, project_public

if TYPE_CHECKING:
    from balatro_horizons.evidence.collect.acceptance import Audit


@dataclass(frozen=True)
class ActionRow:
    """One public action, a before-state capture, and an after-state assertion."""

    name: str
    action: Any
    capture: Any = lambda _audit: None
    expected: Any = lambda _audit, _before: None


def run_action_table(audit: Audit, rows: list[ActionRow]) -> None:
    for row in rows:
        before = row.capture(audit)
        audit.take(row.action(audit))
        row.expected(audit, before)


def _reverse_ids(audit: Audit, area: str) -> list[str]:
    return [card.id for card in reversed(getattr(audit.obs.state, area))]


def _assert_order(area: str):
    def check(audit: Audit, before: list[str]) -> None:
        assert [card.id for card in getattr(audit.obs.state, area)] == before[::-1]

    return check


def _expect_discard(audit: Audit, before: int) -> None:
    assert audit.obs.state.resources.discards == before - 1


def _expect_shop(audit: Audit, _before: Any) -> None:
    assert audit.obs.phase == "SHOP"


def _expect_jokers(audit: Audit, _before: Any) -> None:
    assert len(audit.obs.state.jokers) == 6


def _expect_consumables(audit: Audit, _before: Any) -> None:
    assert len(audit.obs.state.consumables) == 2


def _expect_two_choices(audit: Audit, _before: Any) -> None:
    assert audit.obs.state.resources.pack_choices_remaining == 2


def _expect_one_choice(audit: Audit, _before: Any) -> None:
    assert audit.obs.state.resources.pack_choices_remaining == 1


def _expect_negative_money(audit: Audit, _before: Any) -> None:
    assert float(audit.obs.state.resources.money) < 0


def _expect_round_eval(audit: Audit, _before: Any) -> None:
    assert audit.obs.phase == "ROUND_EVAL" and not audit.game.terminal_status()


def _expect_win(audit: Audit, _before: Any) -> None:
    assert audit.game.terminal_status() == "WIN"


def _expect_progress(audit: Audit, _before: Any) -> None:
    assert audit.obs.state.progress.blind == "Big"


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
            expected=_assert_order("hand"),
        ),
        ActionRow(
            "discard",
            lambda current: {"type": "discard", "card_ids": [current.obs.state.hand[0].id]},
            capture=lambda current: current.obs.state.resources.discards,
            expected=_expect_discard,
        ),
    ]
    if "skip_pack" in audit.obs.available_action_types:
        rows.insert(
            1,
            ActionRow(
                "skip_pack",
                lambda _current: {"type": "skip_pack"},
                expected=_expect_progress,
            ),
        )
    else:
        rows[0] = ActionRow(skip_blind.name, skip_blind.action, expected=_expect_progress)
    return rows


def _round_rows() -> list[ActionRow]:
    return [
        ActionRow(
            "play_hand",
            lambda current: {
                "type": "play_hand",
                "card_ids": [current.obs.state.hand[0].id],
            },
            expected=_expect_round_eval,
        ),
        ActionRow("cash_out", lambda _current: {"type": "cash_out"}, expected=_expect_shop),
    ]


def _buy_offer(audit: Audit, kind: str, *, mode: str = "acquire") -> dict:
    offer = next(item for item in audit.obs.state.offers if item.kind == kind)
    if mode == "buy_and_use":
        assert offer.buy_and_use_allowed
    else:
        assert offer.acquire_allowed
    return {"type": "buy", "offer_id": offer.id, "mode": mode, "target_ids": []}


def _inventory_rows() -> list[ActionRow]:
    rows = [
        ActionRow(
            "reorder",
            lambda current: {
                "type": "reorder",
                "area": "jokers",
                "ordered_ids": _reverse_ids(current, "jokers"),
            },
            capture=lambda current: [card.id for card in current.obs.state.jokers],
            expected=_assert_order("jokers"),
        ),
        ActionRow(
            "reorder",
            lambda current: {
                "type": "reorder",
                "area": "consumables",
                "ordered_ids": _reverse_ids(current, "consumables"),
            },
            capture=lambda current: [card.id for card in current.obs.state.consumables],
            expected=_assert_order("consumables"),
        ),
        ActionRow(
            "buy",
            lambda current: _buy_offer(current, "joker"),
            expected=_expect_jokers,
        ),
        ActionRow(
            "buy",
            lambda current: _buy_offer(current, "consumable", mode="buy_and_use"),
            expected=_expect_consumables,
        ),
    ]
    rows.extend(
        ActionRow(
            "sell",
            lambda current: {"type": "sell", "owned_id": current.obs.state.jokers[0].id},
        )
        for _index in range(2)
    )
    rows.extend(
        [
            ActionRow("buy", lambda current: _buy_offer(current, "voucher")),
            ActionRow(
                "buy",
                lambda current: _buy_offer(current, "pack"),
                expected=_expect_two_choices,
            ),
        ]
    )
    return rows


def _pack_rows() -> list[ActionRow]:
    def choose(current):
        return {
            "type": "choose_pack",
            "offer_id": current.obs.state.offers[0].id,
            "target_ids": [],
        }
    return [
        ActionRow("choose_pack", choose, expected=_expect_one_choice),
        ActionRow("choose_pack", choose, expected=_expect_shop),
    ]


def _credit_rows() -> list[ActionRow]:
    return [
        ActionRow("buy", lambda current: _buy_offer(current, "pack")),
        ActionRow(
            "skip_pack",
            lambda _current: {"type": "skip_pack"},
            expected=_expect_shop,
        ),
        ActionRow(
            "reroll_shop",
            lambda _current: {"type": "reroll_shop"},
            expected=_expect_negative_money,
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


def _assert_easy_blind(audit: Audit) -> None:
    assert audit.obs.state.resources.target == "1"


def _assert_shop_inventory(audit: Audit) -> None:
    assert len(audit.obs.state.jokers) == 5 and len(audit.obs.state.consumables) == 2


def _assert_credit(audit: Audit) -> None:
    assert audit.obs.state.resources.money == "-5"
    assert audit.obs.state.resources.credit_limit == "20"


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
    fixture(audit, "easy_blind", _assert_easy_blind)
    run_action_table(audit, _round_rows()[:1])
    audit.capture()
    run_action_table(audit, _round_rows()[1:])
    fixture(audit, "shop", _assert_shop_inventory)
    run_action_table(audit, _inventory_rows())
    audit.capture()
    run_action_table(audit, _pack_rows())
    fixture(audit, "another_pack")
    run_action_table(audit, _credit_rows())
    fixture(audit, "credit", _assert_credit)
    audit.capture()
    run_action_table(audit, _consumable_rows())
    audit.capture()
    fixture(audit, "mask_jokers", _assert_masked_projection)


def shop_action_types(*, tag_pack_opened: bool = True) -> list[str]:
    """Return the golden-comparable action order without executing a game."""
    available = ["skip_pack"] if tag_pack_opened else []
    audit = SimpleNamespace(obs=SimpleNamespace(available_action_types=available))
    rows = [
        *_shop_rows(audit),
        *_round_rows(),
        *_inventory_rows(),
        *_pack_rows(),
        *_credit_rows(),
        *_consumable_rows(),
    ]
    return [row.name for row in rows]


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
            expected=_expect_win,
        ),
    ]
