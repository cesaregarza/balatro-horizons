#!/usr/bin/env python3
"""Print a compact local-worker status without dumping episodes or session tokens."""

import json
from time import perf_counter

from balatro_horizons.operator_client import operator_request


def configure_parser(parser):
    parser.description = __doc__
    parser.add_argument(
        "--fail-if-running", action="store_true", help="Exit 2 if the worker has an active run"
    )
    parser.add_argument("--timing", action="store_true", help="Include bootstrap + status latency")
    parser.set_defaults(operation_handler=run, operation_parser=parser)


def run(args):
    started = perf_counter()
    try:
        status = operator_request("/operator/status")
    except ValueError:
        print(json.dumps({"error": "WORKBENCH_STATUS_UNAVAILABLE"}))
        return 1
    result = {key: status[key] for key in ("running", "active_episode", "error")}
    if args.timing:
        result["response_ms"] = round((perf_counter() - started) * 1000, 2)
    print(json.dumps(result, sort_keys=True))
    return 2 if args.fail_if_running and (status["running"] or status["active_episode"]) else 0
