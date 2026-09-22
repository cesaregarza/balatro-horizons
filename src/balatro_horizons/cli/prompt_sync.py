"""Embed ALWAYS-LOADED.md in the current prompt; check freshness by default."""

import os
import tempfile
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.harness.instructions import BEGIN as BEGIN
from balatro_horizons.harness.instructions import END as END
from balatro_horizons.harness.instructions import render as render


def configure_parser(parser):
    parser.description = __doc__
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true", help="atomically update the selected prompt")
    parser.set_defaults(operation_handler=run, operation_parser=parser)
    return parser


def sync(root: Path, *, write: bool = False) -> bool:
    root = root.resolve()
    if root.is_relative_to("/mnt"):
        raise ValueError("Use a native Linux checkout")
    directory = root / "configs/prompts"
    source = directory / "ALWAYS-LOADED.md"
    target = directory / "harness.txt"
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


def run(args):
    parser = args.operation_parser
    try:
        current = sync(args.root, write=args.write)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Prompt instruction sync failed: {error}\n")
    if not current:
        parser.exit(1, "Persistent instructions are stale; run with --write.\n")
    print("Persistent instructions are included in the harness prompt.")
    return 0
