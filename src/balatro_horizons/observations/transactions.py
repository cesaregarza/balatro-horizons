"""A public quote and observed balance change are not an instrumented charge."""

from balatro_horizons.actions.validation import InvalidAction, amount
from balatro_horizons.contracts import PublicTransaction


def transaction_receipt(before, after, action):
    charge = proceeds = None
    if action.type in ("buy", "choose_pack"):
        item = next((c for c in before.state.offers if c.id == action.offer_id), None)
        charge = item.price if item else None
    elif action.type == "sell":
        item = next(
            (c for c in before.state.jokers + before.state.consumables if c.id == action.owned_id),
            None,
        )
        proceeds = item.sell_price if item and not item.face_down else None
    elif action.type == "reroll_shop":
        charge = before.state.resources.shop_reroll_cost
    elif action.type == "reroll_boss":
        charge = before.state.resources.boss_reroll_cost
    else:
        return None
    old, new = before.state.resources.money, after.state.resources.money
    try:
        change = str(amount(new) - amount(old))
    except InvalidAction:
        change = None
    # Net cash may also include triggered income/expenses. No actual transaction
    # charge is asserted without a separate native measurement of that charge.
    return PublicTransaction(
        quoted_cash_charge=charge,
        quoted_cash_proceeds=proceeds,
        cash_before=old,
        cash_after=new,
        net_cash_change=change,
    )
