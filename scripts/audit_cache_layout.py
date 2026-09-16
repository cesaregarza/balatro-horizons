#!/usr/bin/env python3
"""Compare recorded public requests with a stable cache prefix; no game or API calls."""

import argparse
import json
from pathlib import Path

from audit_transcript_efficiency import audit, size, stats

from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.agents.skills import load_guide
from balatro_horizons.config import load_config
from balatro_horizons.contracts import Observation
from balatro_horizons.storage.journal import Store, digest


def candidates(store, eid, config, model_name):
    events = store.events(eid)  # Verify the immutable source hash chain first.
    skills = load_guide()[1] if config.skills != "none" else ()
    model = config.models[model_name]
    policy = DirectProvider(model, config.budgets)
    observations = {}
    source = None
    rows = []
    for event in events:
        if event["type"] == "observation":
            observations[event["observation_id"]] = event["payload"]
        elif event["type"] == "agent_context":
            source = event
        elif event["type"] == "provider_request":
            if source is None or source["observation_id"] != event["observation_id"]:
                raise ValueError("MISSING_SOURCE_CONTEXT")
            obs = Observation.model_validate(observations[event["observation_id"]])
            previous = source["payload"]
            # Preserve exactly the recorded pre-call notes and counters. Only the
            # request layout changes; this audit never claims gameplay equivalence.
            obs.memory = previous["context"]["observation"]["memory"]
            ctx, delivered = decision_context(
                obs,
                previous["exchanges"],
                interface=model.settings["harness_interface"],
                skills=skills,
                byte_limit=config.budgets.max_request_bytes,
            )
            ctx["observation"]["remaining_budget"] = previous["context"]["observation"][
                "remaining_budget"
            ]
            body = policy.request(ctx, delivered)
            rows.append(
                {
                    "source_sequence": event["sequence"],
                    "observation_id": obs.observation_id,
                    "phase": obs.phase,
                    "source_context_sequence": source["sequence"],
                    "observation": obs,
                    "body": body,
                }
            )
    policy.client.close()
    return rows


def readonly_store(root):
    root = Path(root).resolve()
    if root.is_relative_to("/mnt"):
        raise ValueError("MOUNTED_PATH_OUTSIDE_SCOPE")
    store = Store.__new__(Store)
    store.root = root
    return store


def compare(store, eid, config, model_name):
    baseline = audit(store, eid)
    rows = candidates(store, eid, config, model_name)
    prefixes = [
        {"tools": row["body"]["tools"], "developer": row["body"]["input"][0]} for row in rows
    ]
    return {
        "evaluation_eligible": False,
        "source_episode_id": eid,
        "source_journal_head": baseline["journal_head"],
        "request_count": len(rows),
        "measure": "UTF-8 JSON bytes, not billed tokens; candidate has not called a provider",
        "baseline_usage": baseline["usage"],
        "before": {
            "unique_tools": baseline["component_unique_values"]["tools"],
            "unique_instructions": baseline["component_unique_values"]["instructions"],
            "request_bytes": baseline["component_bytes"]["request_body"],
        },
        "after": {
            "unique_prefixes": len({digest(p) for p in prefixes}),
            "prefix_bytes": stats([size(p) for p in prefixes]),
            "request_bytes": stats([size(r["body"]) for r in rows]),
            "maximum_conservative_input_bound": max(
                len(json.dumps(r["body"], ensure_ascii=False).encode()) + 4096 for r in rows
            ),
        },
        "rows": [
            {
                "index": i,
                "source_sequence": r["source_sequence"],
                "observation_id": r["observation_id"],
                "phase": r["phase"],
                "helper_messages": len(r["body"]["input"]) - 2,
                "prefix_sha256": digest(prefixes[i]),
                "request_sha256": digest(r["body"]),
            }
            for i, r in enumerate(rows)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Source journal data root")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to("/mnt") or args.config.resolve().is_relative_to("/mnt"):
        parser.error("Mounted paths are outside scope")
    result = compare(
        readonly_store(args.root), args.episode_id, load_config(args.config), args.model
    )
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
