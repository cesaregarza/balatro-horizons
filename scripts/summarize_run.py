#!/usr/bin/env python3
"""Group one public run into rounds, purchases and an action ledger.

No game or provider is contacted. Reading a whole run records review exposure.
"""

import argparse
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.decision_ledger import summarize as summarize
from balatro_horizons.review.decision_ledger import summary_input as summary_input
from balatro_horizons.storage.journal import Store, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--markdown-output", type=Path, help="Also write an ante-grouped decision recap"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    for path in (args.output, args.markdown_output):
        if path is not None and path.exists():
            parser.error("Output already exists; choose a new filename")
    if args.markdown_output and args.output.resolve() == args.markdown_output.resolve():
        parser.error("JSON and Markdown outputs must be different files")
    store = Store(args.data_dir)
    result = build_summary(store, args.episode_id)
    markdown = None
    if args.markdown_output:
        from decision_summary import render_decisions

        markdown = render_decisions(result)
        scan({"markdown": markdown}, [store.manifest(args.episode_id, True).get("seed")])
    atomic_json(args.output, result, immutable=True)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown_output.open("x", encoding="utf8") as stream:
            stream.write(markdown)
    print(f"Recorded {len(result['actions'])} actions in {args.output}")


if __name__ == "__main__":
    main()
