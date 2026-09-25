"""Explicit dollar ceilings; missing configuration never means uncapped."""

from typing import Annotated, Literal

from pydantic import Field

DollarCap = (
    Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
    | Annotated[int, Field(gt=0, strict=True)]
    | Literal["uncapped"]
)


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
