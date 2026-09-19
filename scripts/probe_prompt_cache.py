#!/usr/bin/env python3
"""Bounded API-only cache probe on recorded public states. Never execute game actions."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from audit_cache_layout import candidates, readonly_store
from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.budget import Spending
from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.agents.protocol import Operation
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.config import load_config
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import atomic_json, digest, locked


def select(rows):
    # Cross a phase boundary, change cards/IDs, and include a recorded helper reply.
    indices = [0]
    for predicate in (
        lambda r: r["phase"] == "SELECTING_HAND",
        lambda r: len(r["body"]["input"]) > 2,
        lambda r: r["phase"] == "SHOP",
    ):
        index = next((i for i, row in enumerate(rows) if i not in indices and predicate(row)), None)
        if index is None:
            raise ValueError("SOURCE_LACKS_REQUIRED_PROBE_CASE")
        indices.append(index)
    return [rows[i] for i in indices]


def append(path, event):
    with path.open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Read-only source data root")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--campaign", type=Path, required=True, help="Private durable probe/ledger directory"
    )
    parser.add_argument(
        "--allow-paid", action="store_true", help="Requires explicit operator approval"
    )
    parser.add_argument(
        "--env-file", type=Path, help="Backend credential file; never copied to evidence"
    )
    args = parser.parse_args()
    for path in (args.root, args.config, args.campaign, args.env_file):
        if path and path.resolve().is_relative_to("/mnt"):
            parser.error("Mounted paths are outside scope")
    config = load_config(args.config)
    model, limits = config.models[args.model], config.budgets
    if model.provider != "openai":
        parser.error("This probe requires an OpenAI model")
    if not (
        limits.max_episode_cost_usd
        and limits.max_batch_cost_usd
        and limits.max_episode_cost_usd <= 1
        and limits.max_batch_cost_usd <= 1
        and limits.max_provider_calls == 4
        and limits.max_transport_attempts == 1
    ):
        parser.error("Probe requires four calls, no retries, and caps of at most $1")
    store = readonly_store(args.root)
    rows = select(candidates(store, args.episode_id, config, args.model))
    reserve = (
        limits.max_input_tokens_per_call * model.maximum_input_usd_per_million
        + limits.max_output_tokens_per_call * model.output_usd_per_million
    ) / 1_000_000
    preflight = {
        "source_episode_id": args.episode_id,
        "calls": 4,
        "game_launches": 0,
        "game_actions": 0,
        "reserve_per_call_usd": reserve,
        "worst_case_total_usd": 4 * reserve,
        "episode_cap_usd": limits.max_episode_cost_usd,
        "batch_cap_usd": limits.max_batch_cost_usd,
        "selected": [
            {
                "observation_id": r["observation_id"],
                "phase": r["phase"],
                "source_sequence": r["source_sequence"],
                "request_sha256": digest(r["body"]),
            }
            for r in rows
        ],
    }
    print(json.dumps({"mode": "paid" if args.allow_paid else "preflight", **preflight}), flush=True)
    if not args.allow_paid:
        return 0
    if args.env_file:
        # Parse assignments as data, never source a shell file or echo a value.
        for line in args.env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in ("OPENAI_API_KEY", "export OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = value.strip().strip("\"'")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("MISSING_PROVIDER_CREDENTIAL")
    campaign = args.campaign.resolve()
    campaign.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(campaign / "probe.lock"):
        # A campaign can execute only once; retries require a fresh approved campaign.
        if (campaign / "manifest.json").exists():
            raise ValueError("PROBE_CAMPAIGN_ALREADY_STARTED")
        manifest = {
            **preflight,
            "config": config.public(),
            "implementation_hash": implementation_fingerprint(),
            "evaluation_eligible": False,
            "purpose": "api_cache_probe_on_recorded_public_states",
        }
        atomic_json(campaign / "manifest.json", manifest, immutable=True)
        ReviewService(store).expose(
            args.episode_id,
            "cache_probe_recorded_context",
            max_event_seen=max(r["source_context_sequence"] for r in rows),
            model_identity_seen=True,
        )
        spending = Spending(campaign / "spending.json", limits.max_batch_cost_usd)
        policy = DirectProvider(model, limits)
        results = []
        previous_id = None
        for row in rows:
            body = row["body"]
            if previous_id:
                body["prompt_cache_options"]["comparison_response_id"] = previous_id
            request_id = uuid.uuid4().hex
            try:
                measurement = policy.check_input(body)
            except HarnessFailure as error:
                result = {"request_id": request_id, "error": error.code,
                          "diagnostic": error.public(), "cost_usd": 0,
                          "generation_attempted": False}
                append(campaign / "journal.jsonl", {"type": "error", **result})
                results.append(result)
                break
            append(campaign / "journal.jsonl", {
                "type": "provider_input_check", "request_id": request_id, **measurement,
            })
            spending.reserve(request_id, "cache-probe", reserve, limits.max_episode_cost_usd)
            append(
                campaign / "journal.jsonl",
                {
                    "type": "request",
                    "request_id": request_id,
                    "reserved_usd": reserve,
                    "source_sequence": row["source_sequence"],
                    "body": body,
                },
            )
            started = time.monotonic()
            try:
                response = policy.send(body)
            except ProviderFailure as error:
                result = {
                    "request_id": request_id,
                    "error": error.code,
                    "provider_code": error.provider_code,
                    "usage": "unknown",
                    "cost_usd": reserve,
                }
                append(campaign / "journal.jsonl", {"type": "error", **result})
                results.append(result)
                break
            cost = policy.usage_cost(response, reserve)
            spending.settle(request_id, cost)
            append(
                campaign / "journal.jsonl",
                {"type": "response", "request_id": request_id, "body": response, "cost_usd": cost},
            )
            if response.get("status") == "completed":
                previous_id = response.get("id")
            policy.available_tools = {t["name"] for t in body["tool_choice"]["tools"]}
            operation_status = "valid_helper"
            try:
                operation = Operation.validate_python(policy.parse(response))
                if operation.kind == "action":
                    validate_action(operation.envelope, row["observation"])
                    operation_status = "valid_action_not_executed"
            except (ProtocolFailure, ValidationError, InvalidAction):
                operation_status = "invalid_operation_not_executed"
            result = {
                "request_id": request_id,
                "source_sequence": row["source_sequence"],
                "phase": row["phase"],
                "usage": response.get("usage"),
                "prompt_cache_diagnostics": response.get("prompt_cache_diagnostics"),
                "cost_usd": cost,
                "seconds": round(time.monotonic() - started, 3),
                "operation_status": operation_status,
            }
            results.append(result)
            print(json.dumps(result), flush=True)
        policy.client.close()
        summary = {
            "evaluation_eligible": False,
            "game_launches": 0,
            "game_actions": 0,
            "total_cost_usd": sum(r["cost_usd"] for r in results),
            "results": results,
        }
        atomic_json(campaign / "summary.json", summary, immutable=True)
        print(
            json.dumps(
                {
                    "summary": str(campaign / "summary.json"),
                    "total_cost_usd": summary["total_cost_usd"],
                }
            ),
            flush=True,
        )
        return 1 if any("error" in r for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
