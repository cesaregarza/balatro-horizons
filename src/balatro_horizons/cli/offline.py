"""Run the repository's offline checks using already-installed dependencies."""

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from balatro_horizons.config import ROOT


def commands(root: Path, *, web: bool = False, python: bool = True) -> list[list[str]]:
    checks = [
        [str(root / ".venv/bin/python"), "-m", "pytest", "-q"],
        [str(root / ".venv/bin/ruff"), "check", "src", "tests", "scripts"],
        ["git", "-C", str(root), "diff", "--check", "HEAD"],
    ] if python else []
    if web:
        checks.extend(
            [
                ["npm", "--prefix", str(root / "web"), "run", "build"],
                ["npm", "--prefix", str(root / "web"), "test"],
            ]
        )
    return checks


def run_checks(root: Path, *, web: bool = False, python: bool = True) -> None:
    env = os.environ.copy()
    for credential in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
        env.pop(credential, None)
    for command in commands(root, web=web, python=python):
        print(f"+ {shlex.join(command)}", flush=True)
        subprocess.run(command, cwd=root, env=env, check=True)


def configure_parser(parser):
    parser.description = (
        "Run offline Python checks, optionally the frontend build and browser tests. "
        "Requires uv sync --locked and installed web dependencies. "
        "Does not install dependencies, launch Balatro, or authorize a native release."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository checkout (default: this script's checkout)",
    )
    lane = parser.add_mutually_exclusive_group()
    lane.add_argument("--web", action="store_true", help="also build and test the browser UI")
    lane.add_argument("--web-only", action="store_true",
                      help="only build and test the browser UI (the Python checks run elsewhere)")
    parser.add_argument("--report", type=Path,
                        help="Write a new source-bound success report after all checks pass")
    parser.set_defaults(operation_handler=run, operation_parser=parser)


def run(args):
    parser = args.operation_parser
    root = args.root.resolve()
    if not (root / "src/balatro_horizons").is_dir() or not (root / ".venv/bin/python").is_file():
        parser.error("--root must contain the project and an installed .venv")
    if args.web_only and args.report:
        # Evidence reuse accepts any passed report, so only a complete run may write one.
        parser.error("--web-only cannot write --report")
    try:
        if args.report:
            from balatro_horizons.evidence.provenance import fingerprint_sources, source_files
            from balatro_horizons.storage.journal import atomic_json, now

            before = fingerprint_sources(source_files(root))
        run_checks(root, web=args.web or args.web_only, python=not args.web_only)
        if args.report:
            if fingerprint_sources(source_files(root)) != before:
                print("Source changed during offline checks; report was not written.")
                return 1
            atomic_json(args.report.resolve(), {
                "suite": "check_offline", "status": "passed", "created_at": now(),
                "implementation_hash": before, "web": args.web,
                "commands": commands(root, web=args.web),
            }, immutable=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode if exc.returncode > 0 else 1
    except OSError as exc:
        print(f"Cannot execute offline checks: {exc.strerror}")
        return 1
    print("Offline checks passed; native certification and paid-provider checks remain separate.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    configure_parser(parser)
    return run(parser.parse_args(argv))
