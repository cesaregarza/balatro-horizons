"""Command-line entry points for the same services used by the browser."""

import argparse
import json
import secrets
import sys
from pathlib import Path

from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store


def doctor(config, live=False):
    from balatro_horizons.game.session import NativeFailure, WindowsBridge

    checks = {
        "python": sys.version.split()[0],
        "offline_ready": True,
        "paid_enabled": config.budgets.paid_calls_enabled,
        "native_runtime": "missing",
        "native_certification": "blocked",
        "checkpoint_fidelity": "not_verified",
        "headless": "disabled_pending_parity",
        "blockers": [],
    }
    try:
        from balatro_horizons.agents.skills import prepare_rules

        knowledge = prepare_rules({}, config.skills)
        checks["skills"] = {
            "preset": config.skills,
            "available": len(knowledge.get("skills", [])),
            **knowledge.get("guide", {}),
        }
    except (OSError, ValueError) as error:
        checks["offline_ready"] = False
        checks["blockers"].append(str(error) if str(error).isupper() else "GUIDE_LOAD_FAILED")
    try:
        bridge = WindowsBridge(config.environment)
        lock = bridge.verify_files()
        checks["native_runtime"] = "installed"
        checks["game_version"] = lock["game_version"]
        from balatro_horizons.evidence.certification import require_environment_certificate

        try:
            certificate = require_environment_certificate(lock, config.environment)
            checks["native_certification"] = "passed"
            checks["checkpoint_fidelity"] = "per_decision_seed_prefix_certificates"
            checks["certified_phases"] = certificate.get("phases", [])
        except NativeFailure as error:
            checks["blockers"].append(str(error))
        if live:
            state = bridge.rpc("bh_inspect")
            bridge.verify_identity(state)
            checks["native_handshake"] = "passed"
    except (NativeFailure, OSError, ValueError) as error:
        checks["blockers"].append(str(error) if str(error).isupper() else type(error).__name__)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="bh", description="Balatro Horizons native runs and expert review"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "run", "batch"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, default=ROOT / "configs/pilot.yaml")
        if name == "doctor":
            p.add_argument("--live", action="store_true")
            p.add_argument("--json", action="store_true")
        if name == "run":
            p.add_argument("--agent", default="heuristic")
            p.add_argument("--offline", action="store_true")
            p.add_argument("--calibration", action="store_true")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--seed-file", type=Path)
            p.add_argument("--slot", type=int, default=0)
        if name == "batch":
            p.add_argument("operation", choices=["plan", "run"])
            p.add_argument("--seed-file", type=Path)
            p.add_argument("--agents", nargs="+", default=["model_a", "model_b"])
            p.add_argument("--replicates", type=int, default=2)
            p.add_argument("--plan", type=Path)
            p.add_argument("--offline", action="store_true")
        if name in ("run", "batch"):
            p.add_argument("--allow-paid", action="store_true")
            p.add_argument("--max-episode-cost-usd", type=float)
            p.add_argument("--max-batch-cost-usd", type=float)
    p = sub.add_parser("review")
    p.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--public-origin", help="Exact Tailscale HTTPS origin served by a loopback proxy"
    )
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p = sub.add_parser("replay")
    p.add_argument("operation", choices=["verify"])
    p.add_argument("--episode-id", required=True)
    p.add_argument("--decision", type=int, default=0)
    p.add_argument("--mode", choices=["checkpoint", "seed_prefix"], default="checkpoint")
    p = sub.add_parser("branch")
    p.add_argument("--episode-id", required=True)
    p.add_argument("--decision", type=int, required=True)
    p.add_argument(
        "--mode",
        choices=[
            "agent_continue",
            "single_action_override",
            "short_human_sequence",
            "human_takeover",
        ],
        required=True,
    )
    p.add_argument("--actions-file", type=Path)
    for name in ("report", "export"):
        p = sub.add_parser(name)
        p.add_argument("--batch-id", required=True)
        p.add_argument("--output", type=Path, required=True)
        if name == "export":
            p.add_argument("--public", action="store_true", required=True)
    p = sub.add_parser("seeds")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--count", type=int, default=20)
    sub.add_parser("recover")
    p = sub.add_parser("human")
    p.add_argument("--action-file", type=Path)
    p = sub.add_parser("native")
    p.add_argument("operation", choices=["launch", "stop", "status"])
    args = parser.parse_args(argv)
    try:
        if args.command == "native":
            from balatro_horizons.game.session import WindowsBridge

            bridge = WindowsBridge(load_config().environment)
            if args.operation == "stop":
                bridge.stop()
                result = {"stopped": True}
            else:
                state = bridge.launch() if args.operation == "launch" else bridge.rpc("bh_inspect")
                result = {
                    "phase": state["state"],
                    "identity": state["bh"]["identity"],
                    "ready": state["bh"]["ready"],
                    "busy": state["bh"]["busy"],
                }
            print(json.dumps(result))
            return 0
        if args.command == "doctor":
            result = doctor(load_config(args.config), args.live)
            print(json.dumps(result, indent=2))
            return 1 if result["blockers"] else 0
        if args.command == "review":
            import uvicorn

            from balatro_horizons.api import create_app

            uvicorn.run(
                create_app(args.data_dir, public_origin=args.public_origin),
                host=args.host,
                port=args.port,
            )
            return 0
        store = Store(args.data_dir)
        service = RunService(store, ReviewService(store))
        if args.command in ("run", "batch"):
            cfg = load_config(args.config)
            cfg.budgets.paid_calls_enabled = args.allow_paid
            if args.max_episode_cost_usd is not None:
                cfg.budgets.max_episode_cost_usd = args.max_episode_cost_usd
            if args.max_batch_cost_usd is not None:
                cfg.budgets.max_batch_cost_usd = args.max_batch_cost_usd
        if args.command == "run":
            seed = (
                json.loads(args.seed_file.read_text())["seeds"][args.slot]
                if args.seed_file
                else "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
            )
            if args.dry_run:
                result = {
                    "dry_run": True,
                    "agent": args.agent,
                    "config": cfg.public(),
                    "native_checks": doctor(cfg),
                    "paid_calls_made": 0,
                }
            elif args.agent == "human":
                from balatro_horizons.operator_client import operator_request

                if args.calibration:
                    raise ValueError("CALIBRATION_REQUIRES_SCRIPTED_POLICY")
                result = operator_request(
                    "/runs",
                    "POST",
                    {
                        "agent": "human",
                        "seed": seed,
                        "offline": args.offline,
                        "preset": "smoke" if cfg.environment.stake == "WHITE" else "pilot",
                    },
                )
            else:
                result = service.execute(
                    cfg, args.agent, seed, offline=args.offline, calibration=args.calibration
                )
        elif args.command == "batch":
            from balatro_horizons.evaluation.batches import plan_batch

            if args.operation == "plan":
                if not args.seed_file:
                    raise ValueError("SEED_FILE_REQUIRED")
                result = plan_batch(
                    store, cfg, json.loads(args.seed_file.read_text()), args.agents, args.replicates
                )
            else:
                if not args.plan:
                    raise ValueError("BATCH_PLAN_REQUIRED")
                plan = json.loads(args.plan.read_text())
                result = {
                    "batch_id": service.run_batch(cfg, plan["batch_id"], offline=args.offline)
                }
        elif args.command == "replay":
            from balatro_horizons.evidence.certification import verify_checkpoint

            cfg = Config.model_validate(store.manifest(args.episode_id, True)["config"])
            result = verify_checkpoint(store, cfg, args.episode_id, args.decision, mode=args.mode)
        elif args.command == "branch":
            cfg = Config.model_validate(store.manifest(args.episode_id, True)["config"])
            actions = json.loads(args.actions_file.read_text()) if args.actions_file else []
            if (
                args.mode == "human_takeover"
                or (args.mode == "short_human_sequence" and not actions)
                or store.manifest(args.episode_id)["agent"] == "human"
            ):
                from balatro_horizons.operator_client import operator_request

                result = operator_request(
                    "/branches",
                    "POST",
                    {
                        "episode_id": args.episode_id,
                        "decision": args.decision,
                        "mode": args.mode,
                        "actions": actions,
                    },
                )
            else:
                eid = service.branch(cfg, args.episode_id, args.decision, args.mode, actions)
                service.thread.join()
                result = store.summary(eid)
        elif args.command == "human":
            from balatro_horizons.operator_client import operator_request

            result = (
                operator_request(
                    "/operator/human", "POST", json.loads(args.action_file.read_text())
                )
                if args.action_file
                else operator_request("/operator/human")
            )
        elif args.command == "recover":
            result = {"recovered": store.recover()}
        elif args.command == "seeds":
            from balatro_horizons.evaluation.batches import seed_panel

            result = seed_panel(args.output, args.count)
        elif args.command == "report":
            from balatro_horizons.evaluation.reports import report_batch

            result = report_batch(store, args.batch_id, args.output)
        elif args.command == "export":
            from balatro_horizons.evaluation.reports import export_batch

            result = export_batch(store, args.batch_id, args.output)
        if isinstance(result, dict) and result.get("episode_id") and result.get("outcome"):
            service.review.expose(
                result["episode_id"],
                "cli_result",
                outcome_seen=True,
                model_identity_seen=True,
                max_event_seen=len(store.events(result["episode_id"])) - 1,
            )
        print(json.dumps(result, indent=2))
        return (
            0
            if not isinstance(result, dict)
            or result.get("outcome") not in ("INFRASTRUCTURE_FAILURE", "INVALID_EVALUATION")
            else 1
        )
    except (ValueError, OSError, RuntimeError) as error:
        code = str(error)
        print(
            json.dumps(
                {"error": code if code.isupper() and len(code) < 100 else type(error).__name__}
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
