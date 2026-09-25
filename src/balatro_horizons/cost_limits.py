"""Explicit dollar ceilings; missing configuration never means uncapped."""

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

MAX_FINITE_CAP_USD = 1_000_000


def finite_dollars(value):
    # Bound before converting: enormous integers must refuse, never overflow.
    if type(value) not in (int, float) or not 0 < value <= MAX_FINITE_CAP_USD:
        raise ValueError("INVALID_DOLLAR_CAP")
    return float(value)


DollarCap = (
    Annotated[float, Field(gt=0, le=MAX_FINITE_CAP_USD, allow_inf_nan=False, strict=True),
              BeforeValidator(finite_dollars)]
    | Literal["uncapped"]
)


def require_capped_defaults(episode, campaign):
    if "uncapped" in (episode, campaign):
        raise ValueError("UNCAPPED_REQUIRES_RUN_OVERRIDE")


def headroom(cap, spent):
    return None if cap is None or cap == "uncapped" else cap - spent


def increases_cap(previous, requested):
    return previous is not None and previous != "uncapped" and (
        requested == "uncapped" or requested > previous
    )


def binding_cap(episode, campaign):
    """A standalone run stops at the smaller of its two configured ceilings."""
    if episode is None or campaign is None:
        return None
    finite = [cap for cap in (episode, campaign) if cap != "uncapped"]
    return min(finite) if finite else "uncapped"
