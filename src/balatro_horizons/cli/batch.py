"""Run and batch command implementation."""

import json
import secrets

from balatro_horizons.config import load_config
from balatro_horizons.operator_client import operator_request


def run_batch_command(args, store):
    config = load_config(args.config)
    apply_budget_flags(config, args)
    if args.command == "batch":
        return run_batch(args, store, config)
    return run_episode(args, store, config)


def apply_budget_flags(config, args):
    config.budgets.paid_calls_enabled = args.allow_paid
    if args.max_episode_cost_usd is not None:
        config.budgets.max_episode_cost_usd = args.max_episode_cost_usd
    if args.max_batch_cost_usd is not None:
        config.budgets.max_batch_cost_usd = args.max_batch_cost_usd


def run_episode(args, store, config):
    service = make_service(store)
    seed = read_seed(args)
    if args.dry_run:
        return {"dry_run": True, "agent": args.agent, "config": config.public(), "native_checks": {}, "paid_calls_made": 0}
    if args.agent == "human":
        if args.calibration:
            raise ValueError("CALIBRATION_REQUIRES_SCRIPTED_POLICY")
        return operator_request(
            "/runs", "POST", {"agent": "human", "seed": seed, "offline": args.offline, "preset": "pilot"}
        )
    return service.execute(config, args.agent, seed, offline=args.offline, calibration=args.calibration)


def run_batch(args, store, config):
    if args.operation == "plan":
        if not args.seed_file:
            raise ValueError("SEED_FILE_REQUIRED")
        from balatro_horizons.evaluation.batches import plan_batch

        return plan_batch(store, config, json.loads(args.seed_file.read_text()), args.agents, args.replicates)
    if not args.plan:
        raise ValueError("BATCH_PLAN_REQUIRED")
    plan = json.loads(args.plan.read_text())
    return {"batch_id": make_service(store).run_batch(config, plan["batch_id"], offline=args.offline)}


def make_service(store):
    from balatro_horizons.review.service import ReviewService
    from balatro_horizons.service import RunService

    return RunService(store, ReviewService(store))


def read_seed(args):
    return (
        json.loads(args.seed_file.read_text())["seeds"][args.slot]
        if args.seed_file
        else "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    )
