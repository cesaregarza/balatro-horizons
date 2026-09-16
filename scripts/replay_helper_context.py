#!/usr/bin/env python3
"""Reconstruct one recorded helper follow-up offline; print only sizes and provenance."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import httpx
from audit_cache_layout import readonly_store

from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.agents.input_limits import request_size
from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.contracts import Observation
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.storage.journal import digest


def replay(store, eid, decision, byte_limit=None):
    events = store.events(eid)
    helper = next(
        e
        for e in reversed(events)
        if e["type"] == "helper_result" and e["observation_id"] == decision
    )
    prefix = [e for e in events if e["sequence"] < helper["sequence"]]
    observation = next(e["payload"] for e in reversed(prefix) if e["type"] == "observation")
    context_event = next(e for e in reversed(prefix) if e["type"] == "agent_context")
    request = next(e for e in reversed(prefix) if e["type"] == "provider_request")
    response = next(
        e
        for e in reversed(prefix)
        if e["type"] == "provider_response" and e["request_id"] == request["request_id"]
    )
    frozen = json.loads((store.episode_path(eid, True) / "agent-protocol.json").read_text())
    reference = store.summary(eid)["agent_protocol"]
    if digest(frozen) != reference["hash"]:
        raise ValueError("AGENT_PROTOCOL_SNAPSHOT_MISMATCH")
    limits = Limits.model_validate(frozen["episode_limits"])
    if byte_limit is not None:
        limits.max_request_bytes = byte_limit

    def no_network(request):
        raise AssertionError("Offline reconstruction must not contact a provider")

    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        policy = DirectProvider(ModelConfig.model_validate(frozen["model"]), limits, client)
        saved = context_event["payload"]
        policy.request(saved["context"], saved["exchanges"])
        operation = policy.parse(response["payload"]["body"])
        if operation != helper["payload"]["operation"]:
            raise ValueError("RECORDED_HELPER_MISMATCH")
        exchanges = deepcopy(saved["exchanges"]) + [
            {
                "operation": operation,
                "result": helper["payload"]["result"],
                "tool_call": policy.last_tool_call,
                "provider_turn": policy.last_provider_turn,
            }
        ]
        result = {
            "source_episode_id": eid,
            "decision": decision,
            "source_protocol_hash": reference["hash"],
            "candidate_implementation_hash": implementation_fingerprint(),
            "byte_limit": limits.max_request_bytes,
            "journal_integrity": "passed",
            "game_launches": 0,
            "provider_calls": 0,
            "evaluation_eligible": False,
            "token_count_verified": False,
        }
        try:
            ctx, delivered = decision_context(
                Observation.model_validate(observation),
                exchanges,
                interface=frozen["interface"],
                frozen=frozen,
                byte_limit=limits.max_request_bytes,
            )
            body = policy.request(ctx, delivered)
            expected = [
                item
                for exchange in exchanges
                for item in exchange.get("provider_turn", {}).get("items", [])
            ]
            actual = (
                body.get("input", [])
                if policy.model.provider == "openai"
                else [
                    item
                    for message in body["messages"]
                    if message["role"] == "assistant"
                    for item in message["content"]
                ]
            )
            sequence = iter(actual)
            preserved = all(any(candidate == item for candidate in sequence) for item in expected)
            result.update(
                status="fits_transport",
                request_bytes=request_size(body),
                context_bytes_upper_bound=ctx["context_bytes_upper_bound"],
                provider_items_preserved=preserved,
                retained_exchanges=len(delivered),
            )
        except HarnessFailure as error:
            result.update(status="rejected", diagnostic=error.public())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Linux data directory")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--decision", type=int, required=True)
    parser.add_argument("--byte-limit", type=int)
    args = parser.parse_args()
    if args.root.resolve().is_relative_to("/mnt"):
        parser.error("Use a native Linux data directory")
    if args.byte_limit is not None and args.byte_limit < 1024:
        parser.error("--byte-limit must be at least 1024")
    result = replay(readonly_store(args.root), args.episode_id, args.decision, args.byte_limit)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "fits_transport" else 1


if __name__ == "__main__":
    raise SystemExit(main())
