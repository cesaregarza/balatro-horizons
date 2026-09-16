#!/usr/bin/env python3
"""Audit a recorded public model trace without contacting the game or provider."""

import argparse
import json
from collections import Counter
from statistics import median

from pydantic import ValidationError

from balatro_horizons.agents.protocol import Operation, context, decision_context
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.agents.skills import load_guide, prepare_rules, read_guide
from balatro_horizons.config import ROOT, Config, Limits, ModelConfig
from balatro_horizons.contracts import Observation
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner
from balatro_horizons.storage.journal import Store, atomic_json, digest, locked


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def audit(
    store,
    eid,
    compare=False,
    check_contexts=False,
    with_skills=False,
    probe_guide_reads=False,
    candidate_interface="tools_v2",
):
    skills = load_guide()[1] if with_skills else []
    with locked(store.episode_path(eid) / ".writer.lock"):
        events = store.events(eid)
    requests = [e for e in events if e["type"] == "provider_request"]
    responses = [e for e in events if e["type"] == "provider_response"]
    observations = {e["observation_id"]: e["payload"] for e in events if e["type"] == "observation"}
    rejected = []
    last_operation = None
    for event in events:
        if event["type"] == "agent_operation":
            last_operation = event["payload"]["operation"]
        if event["type"] == "action_rejected":
            row = {"decision": event["observation_id"], "code": event["payload"]["code"]}
            if last_operation is not None:
                row["operation"] = last_operation
                action = last_operation.get("envelope", {}).get("action", {})
                if isinstance(action.get("card_ids"), list):
                    current_ids = [
                        c["id"] for c in observations[event["observation_id"]]["state"]["hand"]
                    ]
                    row["unknown_card_ids"] = [
                        i for i in action["card_ids"] if i not in current_ids
                    ]
                    row["duplicate_card_ids"] = [
                        i for i, n in Counter(action["card_ids"]).items() if n > 1
                    ]
                    row["current_card_ids"] = current_ids
                try:
                    Operation.validate_python(last_operation)
                except ValidationError as error:
                    row["schema_errors"] = error.errors(include_input=False, include_url=False)
            rejected.append(row)
    components = {"tools": [], "observation": [], "history": [], "memory": []}
    for event in requests:
        body = event["payload"]["body"]
        messages = body.get("input", body.get("messages", []))
        current = next(m for m in messages if m.get("role") == "user")
        obs = json.loads(current["content"]).get("observation", {})
        components["tools"].append(size(body["tools"]))
        components["observation"].append(size(obs))
        components["history"].append(size(obs.get("recent_public_events", [])))
        components["memory"].append(size(obs.get("memory", "")))
    result = {
        "episode_id": eid,
        "skill_catalog_in_reconstruction": bool(skills),
        "candidate_interface": candidate_interface,
        "recorded_implementation_hash": next(
            (
                e["payload"].get("implementation_hash")
                for e in events
                if e["type"] == "episode_start"
            ),
            None,
        ),
        "reconstruction_implementation_hash": implementation_fingerprint(),
        "journal_integrity": "passed",
        "evidence_kind": store.manifest(eid)["evidence_kind"],
        "request_count": len(requests),
        "request_component_bytes": {
            k: {"median": median(v), "max": max(v)} for k, v in components.items() if v
        },
        "input_tokens": [
            e["payload"]["body"].get("usage", {}).get("input_tokens") for e in responses
        ],
        "operations": dict(
            Counter(
                e["payload"]["operation"].get("kind", "unknown")
                for e in events
                if e["type"] == "agent_operation"
            )
        ),
        "rejections": rejected,
        "helper_requests": [
            {
                "decision": e["observation_id"],
                "operation": e["payload"]["operation"],
                "result_bytes": size(e["payload"]["result"]),
            }
            for e in events
            if e["type"] == "helper_result"
        ],
        "observation_count": len(observations),
        "evaluation_eligible": False,
    }
    if compare or check_contexts or probe_guide_reads:
        manifest = store.manifest(eid)
        config = manifest["config"]
        model = ModelConfig.model_validate(config["models"][manifest["agent"]])
        model.settings = {**model.settings, "harness_interface": candidate_interface}
        limits = Limits.model_validate(config["budgets"])
        policy = DirectProvider(model, limits)
        previous, candidate, failures = [], [], []
        try:
            for event in requests:
                # Compare first calls only; subsequent helper/error exchanges differ.
                if event["observation_id"] in {row["decision"] for row in candidate + failures}:
                    continue
                observation = Observation.model_validate(observations[event["observation_id"]])
                try:
                    body = policy.request(
                        context(
                            observation,
                            interface=candidate_interface,
                            byte_limit=limits.max_input_tokens_per_call,
                            skills=skills,
                        ),
                        [],
                    )
                    previous.append(size(event["payload"]["body"]))
                    candidate.append({"decision": event["observation_id"], "bytes": size(body)})
                except (ValueError, RuntimeError) as error:
                    failures.append(
                        {"decision": event["observation_id"], "error_type": type(error).__name__}
                    )
        finally:
            policy.client.close()
        result[candidate_interface + "_request_reconstruction"] = {
            "paid_calls": 0,
            "decisions": len(candidate),
            "failures": failures,
            "recorded_request_bytes_median": median(previous) if previous else None,
            "candidate_request_bytes_median": median(r["bytes"] for r in candidate)
            if candidate
            else None,
            "interpretation": "Payload bytes only; not live token usage or model-performance evidence.",
        }
    if check_contexts:
        checked, failures, request_sizes, cleared = 0, [], [], []
        policy = DirectProvider(model, limits)
        try:
            for event in events:
                if event["type"] != "agent_context":
                    continue
                observation = Observation.model_validate(observations[event["observation_id"]])
                try:
                    ctx, exchanges = decision_context(
                        observation,
                        event["payload"]["exchanges"],
                        interface=candidate_interface,
                        byte_limit=limits.max_input_tokens_per_call,
                        skills=skills,
                    )
                    body = policy.request(ctx, exchanges)
                    request_sizes.append(size(body))
                    cleared.append(
                        len(ctx["observation"].get("retrieval_context", {}).get("cleared", []))
                    )
                    checked += 1
                except (ValueError, RuntimeError) as error:
                    failures.append(
                        {
                            "decision": event["observation_id"],
                            "sequence": event["sequence"],
                            "error_type": type(error).__name__,
                        }
                    )
        finally:
            policy.client.close()
        result["all_context_reconstruction"] = {
            "checked": checked,
            "failures": failures,
            "paid_calls": 0,
            "largest_request_bytes": max(request_sizes, default=0),
            "includes_contexts_without_a_provider_request": True,
            "cleared_exchanges_total": sum(cleared),
            "interpretation": "Rebuilds saved public contexts, including legacy helper payloads; byte bounds only, not a played continuation or API validation.",
        }
    if probe_guide_reads:
        library = prepare_rules({}, "balatro-guide-v1")
        policy = DirectProvider(model, limits)
        runner = Runner(None, Config(budgets=limits), None, policy, rules=library)
        reads = refused = 0
        largest = 0
        failures = []
        try:
            for event in events:
                if event["type"] != "agent_context":
                    continue
                observation = Observation.model_validate(observations[event["observation_id"]])
                for key in library["entries"]:
                    raw = {"kind": "rules", "key": key}
                    policy.last_tool_call = {"name": "read_rules", "arguments": {"key": key}}
                    exchanges = event["payload"]["exchanges"]
                    try:
                        page = runner._fit_guide_result(
                            observation, exchanges, raw, read_guide(library, key)
                        )
                        refused += bool(page.get("error"))
                        ctx, delivered = decision_context(
                            observation,
                            exchanges + [runner._exchange(raw, page)],
                            interface=candidate_interface,
                            byte_limit=limits.max_input_tokens_per_call,
                            skills=library["skills"],
                        )
                        body = policy.request(ctx, delivered)
                        largest = max(largest, size(body))
                        reads += 1
                    except (ValueError, RuntimeError) as error:
                        if len(failures) < 20:
                            failures.append(
                                {
                                    "decision": event["observation_id"],
                                    "key": key,
                                    "error_type": type(error).__name__,
                                }
                            )
        finally:
            policy.client.close()
        result["guide_read_reconstruction"] = {
            "requests_checked": reads,
            "failures": failures,
            "explicit_context_limit_responses": refused,
            "largest_request_bytes": largest,
            "paid_calls": 0,
            "native_actions": 0,
            "interpretation": "One added guide read per recorded context, not a played continuation.",
        }
    ReviewService(store).expose(
        eid,
        "harness_audit",
        outcome_seen=any(e["type"] == "terminal" for e in events),
        model_identity_seen=True,
        max_event_seen=events[-1]["sequence"] if events else -1,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--output", type=str)
    parser.add_argument(
        "--export", help="Write a scanned, immutable public export to this JSON path"
    )
    parser.add_argument(
        "--compare-tools-v2",
        action="store_true",
        help="Reconstruct candidate requests without API calls",
    )
    parser.add_argument(
        "--check-contexts",
        action="store_true",
        help="Reconstruct every context including pre-request failures",
    )
    parser.add_argument(
        "--with-skills",
        action="store_true",
        help="Include the current guide catalog and read_skill tool in reconstruction",
    )
    parser.add_argument(
        "--probe-guide-reads",
        action="store_true",
        help="Fit each guide chapter into each recorded context without execution",
    )
    parser.add_argument(
        "--candidate-interface",
        choices=["tools_v2", "tools_v3"],
        default="tools_v2",
        help="Version to reconstruct; historical journals are never rewritten",
    )
    args = parser.parse_args()
    result = audit(
        Store(ROOT / "data"),
        args.episode_id,
        compare=args.compare_tools_v2,
        candidate_interface=args.candidate_interface,
        check_contexts=args.check_contexts,
        with_skills=args.with_skills or args.probe_guide_reads,
        probe_guide_reads=args.probe_guide_reads,
    )
    if args.export:
        from pathlib import Path

        public = episode_export(Store(ROOT / "data"), args.episode_id)
        atomic_json(Path(args.export), public, immutable=True)
        result["public_export"] = {"sha256": digest(public), "privacy_scan": "passed"}
    if args.output:
        from pathlib import Path

        atomic_json(Path(args.output), result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
