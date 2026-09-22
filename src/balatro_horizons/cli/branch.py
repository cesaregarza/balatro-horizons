"""Branch command: always delegate to the running operator gateway."""

import json


def run_branch_command(args):
    actions = json.loads(args.actions_file.read_text()) if args.actions_file else []
    return request_branch(args, actions)


def request_branch(args, actions):
    from balatro_horizons.operator_client import operator_request

    return operator_request(
        "/branches",
        "POST",
        {
            "episode_id": args.episode_id,
            "decision": args.decision,
            "mode": args.mode,
            "actions": actions,
        },
    )
