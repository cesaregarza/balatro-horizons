"""Fail-closed, narrow source patches for the locked BalatroBot revision."""

import hashlib

REARRANGE_SHA256 = "89a24220f492102a63d5b701972b0881cbc0e6063845634ff9b6f252683b6b9f"


def patch_rearrange(source: str) -> str:
    """Allow owned-card ordering at blind selection and round evaluation.

    Both dispatch admission and completion must accept these phases. The upstream
    hand-specific guard and permutation executor remain unchanged.
    """
    if hashlib.sha256(source.encode()).hexdigest() != REARRANGE_SHA256:
        raise ValueError("UNEXPECTED_REARRANGE_SOURCE")
    admission = "requires_state = { G.STATES.SELECTING_HAND, G.STATES.SHOP, G.STATES.SMODS_BOOSTER_OPENED },"
    completion = "            G.STATE == G.STATES.SHOP\n"
    if source.count(admission) != 1 or source.count(completion) != 2:
        raise ValueError("UNEXPECTED_REARRANGE_PATCH_CONTEXT")
    return source.replace(
        admission,
        "requires_state = { G.STATES.SELECTING_HAND, G.STATES.SHOP, G.STATES.SMODS_BOOSTER_OPENED, G.STATES.BLIND_SELECT, G.STATES.ROUND_EVAL },",
    ).replace(
        completion,
        completion + "            or G.STATE == G.STATES.BLIND_SELECT\n"
        "            or G.STATE == G.STATES.ROUND_EVAL\n",
    )
