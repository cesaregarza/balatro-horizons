#!/usr/bin/env python3
"""Find Git revisions matching an accepted Balatro source fingerprint.

Example: PATH=.venv/bin:$PATH scripts/find_certified_source_revision.py \
    --root . --fingerprint <accepted_implementation_hash>

Only Git's public source tree is read. No private certificate, native game, or
provider is opened; the fingerprint must be supplied explicitly.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from reuse_native_evidence import native_path, revision_sources

from balatro_horizons.engine.provenance import fingerprint_sources


def fingerprint(value):
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("fingerprint must be 64 lowercase hex characters")
    return value


def matching_revisions(root, accepted):
    revisions = subprocess.check_output(
        ["git", "-C", str(root), "rev-list", "--all", "--", "src/balatro_horizons"],
        text=True,
    ).splitlines()
    matches = []
    for revision in revisions:
        commit, sources = revision_sources(root, revision)
        if fingerprint_sources(sources) == accepted:
            matches.append(commit)
    return revisions, matches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Native Linux Git checkout")
    parser.add_argument("--fingerprint", required=True, type=fingerprint)
    args = parser.parse_args(argv)
    try:
        root = native_path(args.root)
        revisions, matches = matching_revisions(root, args.fingerprint)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"REVISION_SEARCH_FAILED: {type(error).__name__}\n")
    print(json.dumps({
        "fingerprint": args.fingerprint,
        "scanned_revisions": len(revisions),
        "matches": matches,
    }, sort_keys=True))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
