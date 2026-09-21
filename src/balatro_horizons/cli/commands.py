"""Small dispatch layer; subcommand behavior stays in bounded helpers."""

import json

from balatro_horizons.config import Config, load_config
from balatro_horizons.operator_client import operator_request
from balatro_horizons.storage.journal import Store

from .batch import run_batch_command
from .branch import run_branch_command
from .doctor import doctor
from .evidence import run_evidence_command
from .native import run_native_command
from .review import run_review_command


def dispatch(args):
    if args.command == "evidence":
        return run_evidence_command(args)
    if args.command == "native":
        return run_native_command(args)
    if args.command == "doctor":
        return doctor_command(args)
    if args.command == "review":
        return run_review_command(args)
    if args.command == "branch":
        return run_branch_command(args)
    store = Store(args.data_dir)
    if args.command in ("run", "batch"):
        return run_batch_command(args, store)
    if args.command == "replay":
        return replay_command(args, store)
    if args.command == "human":
        return human_command(args)
    return data_command(args, store)


def doctor_command(args):
    return doctor(load_config(args.config), args.live)


def replay_command(args, store):
    from balatro_horizons.evidence.certification import verify_checkpoint

    config = Config.model_validate(store.manifest(args.episode_id, True)["config"])
    return verify_checkpoint(store, config, args.episode_id, args.decision, mode=args.mode)


def human_command(args):
    payload = json.loads(args.action_file.read_text()) if args.action_file else None
    return (
        operator_request("/operator/human", "POST", payload)
        if payload
        else operator_request("/operator/human")
    )


def data_command(args, store):
    if args.command == "recover":
        return {"recovered": store.recover()}
    if args.command == "seeds":
        from balatro_horizons.evaluation.batches import seed_panel

        return seed_panel(args.output, args.count)
    if args.command == "report":
        from balatro_horizons.evaluation.reports import report_batch

        return report_batch(store, args.batch_id, args.output)
    from balatro_horizons.evaluation.reports import export_batch

    return export_batch(store, args.batch_id, args.output)
