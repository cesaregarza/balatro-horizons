#!/usr/bin/env python3
"""Describe a private replay divergence without printing seeds or hidden card values."""

import argparse
import json
from pathlib import Path


def differing_paths(left, right, path=""):
    if type(left) is not type(right):
        yield path, "type"
    elif isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                yield path + "." + str(key), "presence"
            else:
                yield from differing_paths(left[key], right[key], path + "." + str(key))
    elif isinstance(left, list):
        if len(left) != len(right):
            yield path, "length"
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            yield from differing_paths(a, b, f"{path}[{index}]")
    elif left != right:
        yield path, "value"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--expected-state", type=Path,
                        help="Original private raw-state file when the artifact stores only its hash")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    data = json.loads(args.artifact.read_text())
    expected = (json.loads(args.expected_state.read_text()) if args.expected_state else data["expected"])
    differences = list(differing_paths(expected, data["actual"]))
    print(
        json.dumps(
            {
                "decision": data.get("decision"),
                "difference_count": len(differences),
                "first_differences": differences[: args.limit],
            }
        )
    )


if __name__ == "__main__":
    main()
