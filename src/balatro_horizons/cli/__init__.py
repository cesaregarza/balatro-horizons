"""Command-line entry point assembled from one module per command family."""

import json
import sys

from .commands import dispatch
from .doctor import doctor
from .parser import build_parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if hasattr(args, "operation_handler"):
        # These migrated commands own their text/JSON and nonzero exit codes.
        # Keep their original exception behavior outside the run API's envelope.
        return args.operation_handler(args)
    try:
        result = dispatch(args)
        if result is not None:
            print(json.dumps(result, indent=2))
        if args.command == "doctor":
            return 1 if result["blockers"] else 0
        return 0 if not failed_result(result) else 1
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps(error_payload(error)), file=sys.stderr)
        return 1


def failed_result(result):
    return isinstance(result, dict) and result.get("outcome") in (
        "INFRASTRUCTURE_FAILURE",
        "INVALID_EVALUATION",
    )


def error_payload(error):
    code = str(error)
    return {"error": code if code.isupper() and len(code) < 100 else type(error).__name__}


__all__ = ["doctor", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
