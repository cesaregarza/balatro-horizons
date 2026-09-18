#!/usr/bin/env python3
"""Plan, verify or explicitly fund a continuation through the owning web worker."""

import argparse
import json

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.config import ROOT
from balatro_horizons.engine.certification import read_checkpoint
from balatro_horizons.operator_client import operator_request
from balatro_horizons.storage.journal import Store


def continuation_plan(store, eid):
    terminal = store.summary(eid)
    if not terminal or terminal.get("outcome") not in ("BUDGET_EXHAUSTED", "CAMPAIGN_INTERRUPTED"):
        raise ValueError("PARENT_NOT_COST_EXHAUSTED")
    boundary = next(e for e in reversed(store.events(eid)) if e["type"] == "observation")
    ledger = json.loads((store.episode_path(eid, True) / "spending.json").read_text())
    return {
        "episode_id": eid, "decision": boundary["observation_id"],
        "phase": boundary["payload"]["phase"], "parent_terminal_hash": terminal["journal_head"],
        "original_spent_usd": terminal["cost_usd"],
        "all_attempts_committed_usd": sum(e["cost"] for e in ledger.values()),
        "unsettled_usd": sum(e["cost"] for e in ledger.values() if not e["settled"]),
        "launches": {"restoration_verification": 3, "paid_continuation": 1},
        "reason": "Three fresh restorations and same-action probes, then one restored model run.",
        "funding": "Explicit combined cap includes the original run and all continuation attempts.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Read-only launch plan (default)")
    mode.add_argument("--verify", action="store_true", help="Three unpaid restoration checks; requires Windows authorization")
    mode.add_argument("--start", action="store_true", help="Start the paid continuation; requires explicit spending authorization")
    parser.add_argument("--combined-cap-usd", type=float)
    args = parser.parse_args(argv)
    if args.start != (args.combined_cap_usd is not None):
        parser.error("--start requires --combined-cap-usd; other modes do not accept spending caps")
    store = Store(ROOT / "data")
    try:
        plan = continuation_plan(store, args.episode_id)
        if args.verify:
            checkpoint = read_checkpoint(store, args.episode_id, plan["decision"])
            action = Baseline("heuristic").decide(
                {"observation": checkpoint["observation"]}, []
            )["envelope"]["action"]
            result = operator_request("/verify", "POST", {
                "mode": "checkpoint_probe", "episode_id": args.episode_id,
                "decision": plan["decision"], "probe_action": action,
            }, timeout=600)
            print(json.dumps({k: result[k] for k in (
                "status", "certificate_id", "completed_repetitions", "failures")}, sort_keys=True))
            return 0 if result["status"] == "passed" else 1
        if args.start:
            result = operator_request(f"/operator/episodes/{args.episode_id}/continue-budget", "POST", {
                "combined_cap_usd": args.combined_cap_usd,
                "parent_terminal_hash": plan["parent_terminal_hash"],
            })
            print(json.dumps(result, sort_keys=True))
        else:
            print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error) if str(error).isupper() else type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
