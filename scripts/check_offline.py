#!/usr/bin/env python3
"""Run the repository's offline checks using already-installed dependencies."""

import argparse
import os
import shlex
import subprocess
from pathlib import Path


def commands(root: Path, *, web: bool = False) -> list[list[str]]:
    checks = [
        [str(root / ".venv/bin/python"), "-m", "pytest", "-q"],
        [str(root / ".venv/bin/ruff"), "check", "src", "tests", "scripts"],
        ["git", "-C", str(root), "diff", "--check", "HEAD"],
    ]
    if web:
        checks.extend(
            [
                ["npm", "--prefix", str(root / "web"), "run", "build"],
                ["npm", "--prefix", str(root / "web"), "test"],
            ]
        )
    return checks


def run_checks(root: Path, *, web: bool = False) -> None:
    env = os.environ.copy()
    for credential in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(credential, None)
    for command in commands(root, web=web):
        print(f"+ {shlex.join(command)}", flush=True)
        subprocess.run(command, cwd=root, env=env, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run offline Python checks, optionally the frontend build and browser tests. "
            "Requires uv sync --locked and installed web dependencies. "
            "Does not install dependencies, launch Balatro, or authorize a native release."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository checkout (default: this script's checkout)",
    )
    parser.add_argument("--web", action="store_true", help="also build and test the browser UI")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "src/balatro_horizons").is_dir() or not (root / ".venv/bin/python").is_file():
        parser.error("--root must contain the project and an installed .venv")
    try:
        run_checks(root, web=args.web)
    except subprocess.CalledProcessError as exc:
        return exc.returncode if exc.returncode > 0 else 1
    except OSError as exc:
        print(f"Cannot execute offline checks: {exc.strerror}")
        return 1
    print("Offline checks passed; native certification and paid-provider checks remain separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
