"""Verify settlement rows and cash-out balance transitions in private evidence."""

from __future__ import annotations

import json
from decimal import Decimal

from balatro_horizons.contracts import Settlement
from balatro_horizons.evaluation.economy import economy_metrics
from balatro_horizons.storage.journal import Store


def _observations(events: list[dict]) -> dict[int, dict]:
    return {
        event["payload"]["observation_id"]: event["payload"]
        for event in events
        if event["type"] == "observation"
    }


def _cashout_events(events: list[dict]) -> list[dict]:
    return [
        event
        for event in events
        if event["type"] == "action_commit"
        and event["payload"]["action"]["type"] == "cash_out"
    ]


def _check_settlement(store: Store, eid: str, observations: dict[int, dict], event: dict) -> str | None:
    decision = event["payload"]["observation_id"]
    before, after = observations[decision], observations[decision + 1]
    settlement = Settlement.model_validate(before["state"]["settlement"])
    evidence = json.loads(
        (store.episode_path(eid, True) / f"raw-{decision}.json").read_text()
    )
    native = Settlement.model_validate(evidence["raw_engine"]["bh"]["settlement"])
    assert settlement == native, "PUBLIC_SETTLEMENT_DIFFERS_FROM_NATIVE_ROWS"
    assert before["phase"] == "ROUND_EVAL" and after["phase"] == "SHOP"
    assert 0 < len(settlement.rows) <= 7
    assert all(row.label.strip() for row in settlement.rows)
    assert settlement.omitted_rows == 0, "CALIBRATION_REQUIRES_COMPLETE_ROWS"
    assert sum(Decimal(row.dollars) for row in settlement.rows) == Decimal(settlement.total)
    change = Decimal(after["state"]["resources"]["money"]) - Decimal(
        before["state"]["resources"]["money"]
    )
    assert change == Decimal(settlement.total), "CALIBRATION_CASHOUT_BALANCE_MISMATCH"
    interest = [row for row in settlement.rows if row.kind == "interest"]
    if not interest:
        return "omitted"
    assert len(interest) == 1 and Decimal(interest[0].dollars) > 0
    assert "$" in interest[0].label, "INTEREST_RULE_TEXT_MISSING"
    return "interest"


def verify(store: Store, episode_ids: list[str]) -> dict:
    result = dict(cashouts=0, interest_rows=0, omitted_interest_rows=0, other_phases=0)
    for eid in episode_ids:
        events = store.events(eid)
        observations = _observations(events)
        for observation in observations.values():
            if observation["phase"] != "ROUND_EVAL":
                assert observation["state"].get("settlement") is None, (
                    "SETTLEMENT_OUTSIDE_ROUND_EVAL"
                )
                result["other_phases"] += 1
        seen: set[int] = set()
        for event in _cashout_events(events):
            decision = event["payload"]["observation_id"]
            if decision in seen:
                continue
            seen.add(decision)
            kind = _check_settlement(store, eid, observations, event)
            result["interest_rows" if kind == "interest" else "omitted_interest_rows"] += 1
            result["cashouts"] += 1
        metrics = economy_metrics(events)
        assert metrics["settlements_with_unknown_interest"] == 0
        assert metrics["settlements_with_known_interest"] == len(seen)
    assert result["cashouts"] > 0 and result["interest_rows"] > 0
    assert result["omitted_interest_rows"] > 0, "MISSING_ZERO_INTEREST_CASHOUT"
    assert result["other_phases"] > 0, "MISSING_NON_SETTLEMENT_PHASE"
    return result
