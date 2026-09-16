#!/usr/bin/env python3
"""Verify cash-out visibility and balance transitions in completed native release evidence.

Reads hash-checked calibration journals and private snapshots. Launches no game,
uses no provider, and emits only aggregate checks and provenance hashes.
"""

import json
from decimal import Decimal

from balatro_horizons.config import ROOT
from balatro_horizons.contracts import Settlement
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.evaluation.economy import economy_metrics
from balatro_horizons.storage.journal import Store, atomic_json, digest


def verify(store, episode_ids):
    result = dict(cashouts=0, interest_rows=0, omitted_interest_rows=0, other_phases=0)
    for eid in episode_ids:
        events = store.events(eid)
        observations = {
            e["payload"]["observation_id"]: e["payload"]
            for e in events
            if e["type"] == "observation"
        }
        for obs in observations.values():
            if obs["phase"] != "ROUND_EVAL":
                assert obs["state"].get("settlement") is None, "SETTLEMENT_OUTSIDE_ROUND_EVAL"
                result["other_phases"] += 1
        seen = set()
        for event in events:
            if event["type"] != "action_commit" or event["payload"]["action"]["type"] != "cash_out":
                continue
            decision = event["payload"]["observation_id"]
            if decision in seen:
                continue
            seen.add(decision)
            before, after = observations[decision], observations[decision + 1]
            settlement = Settlement.model_validate(before["state"]["settlement"])
            raw = json.loads((store.episode_path(eid, True) / f"raw-{decision}.json").read_text())
            native = Settlement.model_validate(raw["raw_engine"]["bh"]["settlement"])
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
            if interest:
                assert len(interest) == 1 and Decimal(interest[0].dollars) > 0
                assert "$" in interest[0].label, "INTEREST_RULE_TEXT_MISSING"
                result["interest_rows"] += 1
            else:
                result["omitted_interest_rows"] += 1
            result["cashouts"] += 1
        metrics = economy_metrics(events)
        assert metrics["settlements_with_unknown_interest"] == 0
        assert metrics["settlements_with_known_interest"] == len(seen)
    assert result["cashouts"] > 0 and result["interest_rows"] > 0
    assert result["omitted_interest_rows"] > 0 and result["other_phases"] > 0
    return result


def main():
    release = json.loads((ROOT / "reports/verification/native-release.json").read_text())
    lock = json.loads((ROOT / "private/environment.lock.json").read_text())
    assert release["implementation_hash"] == implementation_fingerprint()
    assert release["environment_hash"] == digest(lock)
    eids = [release["fixture"]["episode_id"]]
    eids.extend(run["episode_id"] for run in release["ordinary_runs"].values())
    faults = json.loads((ROOT / "reports/verification/native-faults.json").read_text())
    eids.extend(run["episode_id"] for run in faults["tests"])
    reorder = json.loads((ROOT / "reports/verification/native-reorder.json").read_text())
    assert reorder["implementation_hash"] == release["implementation_hash"]
    assert reorder["environment_hash"] == release["environment_hash"]
    eids.append(reorder["episode_id"])
    store = Store(ROOT / "data")
    for eid in eids:
        checkpoint = json.loads((store.episode_path(eid, True) / "checkpoint-0.json").read_text())
        assert checkpoint["implementation_hash"] == release["implementation_hash"]
        assert digest(checkpoint["game"]["environment"]) == release["environment_hash"]
    checks = verify(store, eids)
    result = {
        "status": "passed",
        "evidence_kind": "NATIVE_CALIBRATION",
        "implementation_hash": release["implementation_hash"],
        "environment_hash": release["environment_hash"],
        "checks": checks,
        "provider_calls": 0,
        "additional_game_launches": 0,
    }
    atomic_json(ROOT / "reports/verification/native-settlement.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
