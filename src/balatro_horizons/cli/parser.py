"""Argument parser construction, kept separate from command execution."""

import argparse
from pathlib import Path

from balatro_horizons.config import ROOT


def build_parser():
    parser = argparse.ArgumentParser(prog="bh", description="Balatro Horizons native runs and expert review")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)
    add_runtime_commands(sub)
    add_review_commands(sub)
    add_evidence_commands(sub)
    add_data_commands(sub)
    add_summarize_command(sub)
    return parser


def add_runtime_commands(sub):
    for name in ("doctor", "run", "batch"):
        parser = sub.add_parser(name)
        parser.add_argument("--config", type=Path, default=ROOT / "configs/pilot.yaml")
        add_command_options(parser, name)
    sub.choices["doctor"].add_argument("--live", action="store_true")
    sub.choices["doctor"].add_argument("--json", action="store_true")
    sub.choices["batch"].add_argument("operation", choices=["plan", "run"])
    sub.choices["batch"].add_argument("--seed-file", type=Path)
    sub.choices["batch"].add_argument("--agents", nargs="+", default=["model_a", "model_b"])
    sub.choices["batch"].add_argument("--replicates", type=int, default=2)
    sub.choices["batch"].add_argument("--plan", type=Path)
    sub.choices["run"].add_argument("--agent", default="heuristic")
    sub.choices["run"].add_argument("--calibration", action="store_true")
    sub.choices["run"].add_argument("--dry-run", action="store_true")
    sub.choices["run"].add_argument("--seed-file", type=Path)
    sub.choices["run"].add_argument("--slot", type=int, default=0)


def add_command_options(parser, name):
    if name in ("run", "batch"):
        parser.add_argument("--offline", action="store_true")
        parser.add_argument("--allow-paid", action="store_true")
        parser.add_argument("--max-episode-cost-usd", type=float)
        parser.add_argument("--max-batch-cost-usd", type=float)


def add_review_commands(sub):
    parser = sub.add_parser("review")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--public-origin")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    for name in ("replay", "branch"):
        add_intervention_parser(sub, name)


def add_intervention_parser(sub, name):
    parser = sub.add_parser(name)
    if name == "replay":
        parser.add_argument("operation", choices=["verify"])
        parser.add_argument("--episode-id", required=True)
        parser.add_argument("--decision", type=int, default=0)
        parser.add_argument("--mode", choices=["checkpoint", "seed_prefix"], default="checkpoint")
    else:
        parser.add_argument("--episode-id", required=True)
        parser.add_argument("--decision", type=int, required=True)
        parser.add_argument("--mode", choices=["agent_continue", "single_action_override", "short_human_sequence", "human_takeover"], required=True)
        parser.add_argument("--actions-file", type=Path)


def add_data_commands(sub):
    for name in ("report", "export"):
        parser = sub.add_parser(name)
        parser.add_argument("--batch-id", required=True)
        parser.add_argument("--output", type=Path, required=True)
        if name == "export":
            parser.add_argument("--public", action="store_true", required=True)
    parser = sub.add_parser("seeds")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    sub.add_parser("recover")
    parser = sub.add_parser("human")
    parser.add_argument("--action-file", type=Path)
    parser = sub.add_parser("native")
    parser.add_argument("operation", choices=["launch", "stop", "status"])


def add_summarize_command(sub):
    parser = sub.add_parser(
        "summarize",
        description=(
            "Group one public run into rounds, purchases and an action ledger. "
            "No game or provider is contacted. Reading a whole run records review exposure."
        ),
    )
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--markdown-output", type=Path, help="Also write an ante-grouped decision recap"
    )
    parser.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)


def add_evidence_commands(sub):
    parser = sub.add_parser("evidence")
    operations = parser.add_subparsers(dest="evidence_operation", required=True)
    plan = operations.add_parser("plan")
    plan.add_argument("--gameplay-only", action="store_true")
    collect = operations.add_parser("collect")
    collect.add_argument("--from-stage")
    collect.add_argument("--episode-id")
    collect.add_argument("--gameplay-only", action="store_true")
    operations.add_parser("certify")
    operations.add_parser("publish")
    reuse = operations.add_parser("reuse")
    reuse.add_argument("--root", required=True, type=Path)
    reuse.add_argument("--candidate", required=True, type=Path)
    reuse.add_argument("--baseline", default="auto")
    reuse.add_argument("--offline-report", required=True, type=Path)
    reuse.add_argument("--apply", action="store_true")
    inspect = operations.add_parser("inspect")
    inspect.add_argument("artifact", type=Path)
    inspect.add_argument("--limit", type=int, default=20)
