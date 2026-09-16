#!/usr/bin/env python3
"""Inspect selected game source members without extracting proprietary files."""

import argparse
import re
import zipfile
from pathlib import Path


def inspect(archive, members, pattern=None, context=2):
    with zipfile.ZipFile(archive) as bundle:
        for name in members:
            lines = bundle.read(name).decode("utf-8").splitlines()
            indices = (
                set(range(len(lines)))
                if not pattern
                else {
                    j
                    for i, line in enumerate(lines)
                    if re.search(pattern, line)
                    for j in range(max(0, i - context), min(len(lines), i + context + 1))
                }
            )
            for i in sorted(indices):
                yield f"{name}:{i + 1}: {lines[i]}"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("members", nargs="+")
    p.add_argument("--pattern", help="Only matching lines, with two lines of context")
    p.add_argument("--context", type=int, default=2, choices=range(0, 501), metavar="0..500")
    args = p.parse_args()
    try:
        for line in inspect(args.archive, args.members, args.pattern, args.context):
            print(line)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, re.error) as error:
        p.exit(1, f"Inspection failed: {type(error).__name__}\n")


if __name__ == "__main__":
    main()
