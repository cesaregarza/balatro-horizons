#!/usr/bin/env python3
"""Print a compact local-worker status without dumping episodes or session tokens."""

import argparse
import json

from balatro_horizons.operator_client import operator_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-if-running", action="store_true", help="Exit 2 if the worker has an active run"
    )
    args = parser.parse_args()
    try:
        status = operator_request("/operator/status")
    except ValueError:
        print(json.dumps({"error": "WORKBENCH_STATUS_UNAVAILABLE"}))
        return 1
    print(
        json.dumps(
            {key: status[key] for key in ("running", "active_episode", "error")}, sort_keys=True
        )
    )
    return 2 if args.fail_if_running and (status["running"] or status["active_episode"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
