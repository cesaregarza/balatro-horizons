"""Plan, verify or explicitly fund a continuation through the owning web worker."""

import argparse
import json
from pathlib import Path

from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.money import validate_caps
from balatro_horizons.operator_client import operator_request
from balatro_horizons.review.run_status import run_status
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
        "launches": {"restoration_verification": 0, "paid_continuation": 1},
        "reason": "One checked restore/replay, then continuation in the same game instance.",
        "funding": "Explicit combined cap includes the original run and all continuation attempts.",
        "status": run_status(store, eid),
    }


def configure_parser(parser):
    parser.add_argument("episode_id")
    parser.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Read-only launch plan (default)")
    mode.add_argument("--verify", action="store_true", help="Optional three-launch diagnostic, not required to start; requires Windows authorization")
    mode.add_argument("--start", action="store_true", help="Start the paid continuation; requires explicit spending authorization")
    parser.add_argument("--combined-cap-usd", type=float)
    parser.add_argument("--authorize-paid", action="store_true",
                        help="Explicitly authorize spending for this numeric-cap continuation")
    parser.add_argument("--accept-compatible-update", action="store_true",
                        help="Explicitly accept a separately validated source-compatible update")
    parser.set_defaults(operation_handler=run)


def verify_plan(store, plan):
    checkpoint = read_checkpoint(store, plan["episode_id"], plan["decision"])
    choice = Baseline("heuristic").decide({"observation": checkpoint["observation"]}, [])
    if choice["kind"] != "action":
        raise ValueError("NO_PUBLIC_LEGAL_PROBE_ACTION")
    result = operator_request("/verify", "POST", {
        "mode": "checkpoint_probe", "episode_id": plan["episode_id"],
        "decision": plan["decision"], "probe_action": choice["envelope"]["action"],
    }, timeout=600)
    return {key: result[key] for key in (
        "status", "certificate_id", "completed_repetitions", "failures")}


def run(args):
    try:
        if args.start != (args.combined_cap_usd is not None):
            raise ValueError("START_REQUIRES_COMBINED_CAP")
        if args.start:
            if not args.authorize_paid:
                raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
            validate_caps(args.combined_cap_usd, args.combined_cap_usd)
        store = Store(args.data_dir)
        plan = continuation_plan(store, args.episode_id)
        if args.verify:
            result = verify_plan(store, plan)
        elif args.start:
            result = operator_request(f"/operator/episodes/{args.episode_id}/continue-budget", "POST", {
                "combined_cap_usd": args.combined_cap_usd,
                "parent_terminal_hash": plan["parent_terminal_hash"],
                "authorize_paid": args.authorize_paid,
                "accept_compatible_update": args.accept_compatible_update,
            })
        else:
            result = plan
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if args.verify and result["status"] != "passed" else 0
    except (ValueError, OSError) as error:
        code = str(error)
        print(json.dumps({"error": code if code.isupper() and len(code) < 100 else type(error).__name__}))
        return 1
