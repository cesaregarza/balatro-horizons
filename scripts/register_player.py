#!/usr/bin/env python3
"""Clone a saved player's harness settings with explicit model and accounting rates.

Previews by default. --apply registers the player through the workbench API;
it preserves budgets, skills and other players and never starts a paid run.
"""

import argparse
import json
import re
from copy import deepcopy

from balatro_horizons.config import ModelConfig
from balatro_horizons.operator_client import operator_request


def settings_payload(config, alias, model):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", alias):
        raise ValueError("INVALID_PLAYER_ALIAS")
    models = deepcopy(config["models"])
    validated = ModelConfig.model_validate(model).model_dump()
    if alias in models and models[alias] != validated:
        raise ValueError("EXISTING_PLAYER_DIFFERS")
    models[alias] = validated
    return {"models": models, "budgets": deepcopy(config["budgets"]), "skills": config["skills"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--clone", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--input-rate", type=float, required=True, help="USD per million input tokens"
    )
    parser.add_argument(
        "--output-rate", type=float, required=True, help="USD per million output tokens"
    )
    parser.add_argument("--cached-input-rate", type=float)
    parser.add_argument("--cache-write-rate", type=float)
    parser.add_argument("--interface", choices=["operate_v1", "tools_v2", "tools_v3", "tools_v4"])
    parser.add_argument("--pricing-date", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = operator_request("/bootstrap")["config"]
    model = {
        **config["models"][args.clone],
        "model": args.model,
        "input_usd_per_million": args.input_rate,
        "output_usd_per_million": args.output_rate,
        "pricing_date": args.pricing_date,
        "cached_input_usd_per_million": args.cached_input_rate,
        "cache_write_input_usd_per_million": args.cache_write_rate,
    }
    if args.interface:
        model["settings"] = {**model["settings"], "harness_interface": args.interface}
    payload = settings_payload(config, args.alias, model)
    if args.apply:
        operator_request("/settings", method="PUT", payload=payload)
        saved = operator_request("/bootstrap")["config"]
        if any(saved[key] != value for key, value in payload.items()):
            raise ValueError("PLAYER_SETTINGS_READBACK_MISMATCH")
    print(
        json.dumps(
            {
                "applied": args.apply,
                "alias": args.alias,
                "model": model,
                "budgets_unchanged": True,
                "paid_run_started": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
