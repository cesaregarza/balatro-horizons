"""Action rows covering inventory reorder, purchase, and sale behavior."""

from __future__ import annotations

from balatro_horizons.evidence.collect.action_rows import ActionRow
from balatro_horizons.evidence.collect.action_rows import buy_offer as _buy_offer
from balatro_horizons.evidence.collect.action_rows import require as _require
from balatro_horizons.evidence.collect.action_rows import reverse_ids as _reverse_ids


def _reorder_row(area: str, message: str) -> ActionRow:
    return ActionRow(
        "reorder",
        lambda current: {
            "type": "reorder",
            "area": area,
            "ordered_ids": _reverse_ids(current, area),
        },
        capture=lambda current: [card.id for card in getattr(current.obs.state, area)],
        expected=lambda current, before: _require(
            [card.id for card in getattr(current.obs.state, area)] == before[::-1], message
        ),
    )


def _purchase_row(kind: str, area: str, count: int, message: str, *, mode: str = "acquire"):
    return ActionRow(
        "buy",
        lambda current: _buy_offer(current, kind, mode=mode),
        expected=lambda current, _before: _require(
            len(getattr(current.obs.state, area)) == count, message
        ),
    )


def inventory_rows() -> list[ActionRow]:
    rows = [
        _reorder_row("jokers", "JOKER_REORDER_NOT_REVERSED"),
        _reorder_row("consumables", "CONSUMABLE_REORDER_NOT_REVERSED"),
        _purchase_row("joker", "jokers", 6, "JOKER_PURCHASE_NOT_VISIBLE"),
        _purchase_row(
            "consumable", "consumables", 2, "CONSUMABLE_PURCHASE_NOT_VISIBLE", mode="buy_and_use"
        ),
    ]
    rows.extend(
        ActionRow(
            "sell",
            lambda current: {"type": "sell", "owned_id": current.obs.state.jokers[0].id},
        )
        for _ in range(2)
    )
    rows.extend(
        [
            ActionRow("buy", lambda current: _buy_offer(current, "voucher")),
            ActionRow(
                "buy",
                lambda current: _buy_offer(current, "pack"),
                expected=lambda current, _before: _require(
                    current.obs.state.resources.pack_choices_remaining == 2,
                    "PACK_PURCHASE_DID_NOT_OPEN_TWO_CHOICES",
                ),
            ),
        ]
    )
    return rows
