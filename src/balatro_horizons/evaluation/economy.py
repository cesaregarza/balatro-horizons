"""Descriptive public-journal measurements, never a savings policy or horizon score."""

from decimal import Decimal, InvalidOperation


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def economy_metrics(events):
    observation = None
    settlements, exits = [], []
    rounds, debt = set(), set()
    for event in events:
        if event["type"] == "observation":
            observation = event["payload"]
            if observation["phase"] == "SELECTING_HAND":
                progress = observation["state"]["progress"]
                key = (progress.get("ante"), progress.get("round_number"), progress.get("blind"))
                money = number(observation["state"]["resources"].get("money"))
                rounds.add(key)
                if money is not None and money < 0:
                    debt.add(key)
        elif event["type"] == "action_commit" and observation is not None:
            payload = event["payload"]
            if payload["observation_id"] != observation["observation_id"]:
                continue
            action = payload["action"]["type"]
            state = observation["state"]
            money = state["resources"].get("money")
            if action == "leave_shop":
                exits.append({"observation_id": payload["observation_id"], "money": money})
            if action != "cash_out":
                continue
            settlement = state.get("settlement")
            interest, basis = None, "not_recorded"
            if settlement is not None:
                rows = [r for r in settlement["rows"] if r["kind"] == "interest"]
                values = [number(r["dollars"]) for r in rows]
                if rows and all(v is not None for v in values):
                    interest, basis = str(sum(values)), "displayed_rows"
                elif not rows and settlement["omitted_rows"] == 0:
                    interest, basis = "0", "no_interest_row_in_complete_breakdown"
                else:
                    basis = "not_fully_displayed"
            chips, target = [number(state["resources"].get(k)) for k in ("chips", "target")]
            settlements.append(
                {
                    "observation_id": payload["observation_id"],
                    "money_before_cash_out": money,
                    "interest": interest,
                    "interest_basis": basis,
                    "displayed_total": settlement["total"] if settlement else None,
                    "score_target_ratio": str(chips / target)
                    if chips is not None and target is not None and target > 0
                    else None,
                }
            )
    known = [number(s["interest"]) for s in settlements if s["interest"] is not None]
    return {
        "version": "public-economy-v1",
        "settlements": settlements,
        "shop_exits": exits,
        "settlements_with_known_interest": len(known),
        "settlements_with_unknown_interest": len(settlements) - len(known),
        "known_interest_total": str(sum(known)) if known else None,
        "played_rounds_observed": len(rounds),
        "played_rounds_observed_in_debt": len(debt),
        "interpretation": "Descriptive public observations; omitted payouts are unknown. No horizon or strategy score.",
    }
