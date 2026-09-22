"""Shared row contract for native action tables and inventory expectations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from balatro_horizons.evidence.collect.acceptance import Audit


@dataclass(frozen=True)
class ActionRow:
    """One public action, a before-state capture, and an after-state assertion."""

    name: str
    action: Any
    capture: Any = lambda _audit: None
    expected: Any = lambda _audit, _before: None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reverse_ids(audit: Audit, area: str) -> list[str]:
    return [card.id for card in reversed(getattr(audit.obs.state, area))]


def buy_offer(audit: Audit, kind: str, *, mode: str = "acquire") -> dict:
    offer = next(item for item in audit.obs.state.offers if item.kind == kind)
    if mode == "buy_and_use":
        assert offer.buy_and_use_allowed
    else:
        assert offer.acquire_allowed
    return {"type": "buy", "offer_id": offer.id, "mode": mode, "target_ids": []}
