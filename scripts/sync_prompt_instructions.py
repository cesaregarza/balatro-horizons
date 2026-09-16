#!/usr/bin/env python3
"""Embed ALWAYS-LOADED.md in the current prompt; check freshness by default."""

import argparse
import os
import tempfile
from pathlib import Path

from balatro_horizons.config import ALWAYS_LOADED_MAX_BYTES

BEGIN = "<!-- BEGIN ALWAYS-LOADED.md -->"
END = "<!-- END ALWAYS-LOADED.md -->"


def render(prompt: str, instructions: str) -> str:
    if not instructions.strip() or len(instructions.encode("utf-8")) > ALWAYS_LOADED_MAX_BYTES:
        raise ValueError(f"ALWAYS-LOADED.md must contain 1-{ALWAYS_LOADED_MAX_BYTES} UTF-8 bytes")
    if BEGIN in instructions or END in instructions:
        raise ValueError("Instructions must not contain generated-block markers")
    block = BEGIN + "\n" + instructions.strip() + "\n" + END
    if BEGIN not in prompt and END not in prompt:
        return prompt.rstrip() + "\n\n" + block + "\n"
    if prompt.count(BEGIN) != 1 or prompt.count(END) != 1:
        raise ValueError("Prompt must contain exactly one complete generated block")
    before, _, remainder = prompt.partition(BEGIN)
    if END not in remainder:
        raise ValueError("Generated-block markers are out of order")
    _, _, after = remainder.partition(END)
    return before + block + after


def sync(root: Path, *, write: bool = False) -> bool:
    root = root.resolve()
    if root.is_relative_to("/mnt"):
        raise ValueError("Use a native Linux checkout")
    directory = root / "configs/prompts"
    source, target = directory / "ALWAYS-LOADED.md", directory / "tools-v5.txt"
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
    parser.add_argument("--write", action="store_true", help="atomically update tools-v5.txt")
    args = parser.parse_args()
    try:
        current = sync(args.root, write=args.write)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Prompt instruction sync failed: {error}\n")
    if not current:
        parser.exit(1, "Persistent instructions are stale; run with --write.\n")
    print("Persistent instructions are included in tools-v5.txt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
