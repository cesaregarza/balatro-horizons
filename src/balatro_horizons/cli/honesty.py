"""bh honesty: score pre-action claims in recorded journals, read-only."""

import json

from balatro_horizons.evaluation.honesty_report import recall, score_runs


def configure_parser(parser):
    parser.description = (
        "Score the claims agents wrote before each action against the next observation. "
        "Reads public journals under --data-dir only: no game, provider or review exposure. "
        "Each proportion is reported on its own, beside the Ante 8 outcome and never combined with it."
    )
    parser.add_argument("--episodes", help="file listing the episode IDs to score, one per line")
    parser.add_argument("--labels", help="hand-labelled recall sample (JSON) to measure extraction recall")
    parser.add_argument("--seed", type=int, default=20261026, help="cluster-bootstrap seed")
    parser.add_argument("--draws", type=int, default=2000, help="cluster-bootstrap draws")
    parser.set_defaults(operation_handler=run)


def run(args):
    episodes = None
    if args.episodes:
        with open(args.episodes, encoding="utf-8") as stream:
            episodes = [line.strip() for line in stream.read().split("\n") if line.strip()]
    report = score_runs(args.data_dir, episodes, seed=args.seed, draws=args.draws)
    if args.labels:
        with open(args.labels, encoding="utf-8") as stream:
            report["recall"] = recall(args.data_dir, json.load(stream))
    print(json.dumps(report, indent=2))
    return 0
