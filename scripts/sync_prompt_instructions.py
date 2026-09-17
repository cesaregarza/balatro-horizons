#!/usr/bin/env python3
"""Embed ALWAYS-LOADED.md in the current prompt; check freshness by default."""

import argparse
import os
import tempfile
from pathlib import Path

from balatro_horizons.agents.instructions import BEGIN as BEGIN
from balatro_horizons.agents.instructions import END as END
from balatro_horizons.agents.instructions import render as render


def sync(root: Path, *, write: bool = False, interface: str = "tools_v6") -> bool:
    if interface not in ("tools_v5", "tools_v6"):
        raise ValueError("Unsupported persistent-instruction interface")
    root = root.resolve()
    if root.is_relative_to("/mnt"):
        raise ValueError("Use a native Linux checkout")
    directory = root / "configs/prompts"
    source = directory / "ALWAYS-LOADED.md"
    target = directory / (interface.replace("_", "-") + ".txt")
    for path in (source, target):
        if not path.resolve().is_relative_to(root):
            raise ValueError("Prompt paths must remain inside the checkout")
    original = target.read_text(encoding="utf-8")
    rendered = render(original, source.read_text(encoding="utf-8"))
    if original == rendered:
        return True
    if not write:
        return False
    # A live worker may freeze the prompt while it is updated: publish whole bytes.
    fd, name = tempfile.mkstemp(prefix=".prompt-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(rendered)
        temporary.chmod(target.stat().st_mode & 0o777)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--interface", choices=("tools_v5", "tools_v6"), default="tools_v6")
    parser.add_argument("--write", action="store_true", help="atomically update the selected prompt")
    args = parser.parse_args()
    try:
        current = sync(args.root, write=args.write, interface=args.interface)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Prompt instruction sync failed: {error}\n")
    if not current:
        parser.exit(1, "Persistent instructions are stale; run with --write.\n")
    print(f"Persistent instructions are included in {args.interface}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
