#!/usr/bin/env python3
"""Check one recorded public decision request; never execute its proposed game action."""

import argparse
import json
import re
import uuid

from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.budget import BudgetExhausted, Spending
from balatro_horizons.agents.protocol import Operation, decision_context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.contracts import Observation
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import Store, atomic_json, digest, locked


def prepare(store, eid, decision, config):
    with locked(store.episode_path(eid) / ".writer.lock"):
        events = store.events(eid)
    source = next(
        e
        for e in reversed(events)
        if e["type"] == "agent_context" and e["observation_id"] == decision
    )
    observation = Observation.model_validate(
        next(
            e["payload"]
            for e in events
            if e["type"] == "observation" and e["observation_id"] == decision
        )
    )
    exchanges = source["payload"]["exchanges"]
    # This diagnostic replays recorded full helper outputs, not already coalesced references.
    if any(
        e["operation"].get("kind") == "inspect" and "sections" not in e["result"] for e in exchanges
    ):
        raise ValueError("PROBE_REQUIRES_RECORDED_FULL_INSPECTION_RESULTS")
    ctx, delivered = decision_context(
        observation,
        exchanges,
        interface="tools_v2",
        byte_limit=config.budgets.max_input_tokens_per_call,
    )
    ReviewService(store).expose(
        eid, "context_probe_input", model_identity_seen=True, max_event_seen=source["sequence"]
    )
    return observation, ctx, delivered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--decision", type=int, required=True)
    parser.add_argument("--revision", default="tools-v2-retrieval")
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{1,40}", args.revision):
        parser.error("invalid revision name")
    config = load_config(ROOT / "configs/luna-tools-smoke.yaml")
    limits, model = config.budgets, config.models["luna"]
    if (
        not limits.max_episode_cost_usd
        or limits.max_episode_cost_usd > 1
        or not limits.max_batch_cost_usd
        or limits.max_batch_cost_usd > 5
    ):
        raise ValueError("SMOKE_EXCEEDS_AUTHORIZED_CAPS")
    config.budgets.paid_calls_enabled = args.allow_paid
    store = Store(ROOT / "data")
    obs, ctx, exchanges = prepare(store, args.episode_id, args.decision, config)
    policy = DirectProvider(model, limits)
    body = policy.request(ctx, exchanges)
    scan(body, [store.manifest(args.episode_id, True).get("seed")])
    preflight = {
        "source_episode_id": args.episode_id,
        "source_decision": args.decision,
        "request_sha256": digest(body),
        "request_bytes": len(json.dumps(body, ensure_ascii=False).encode()),
        "privacy_scan": "passed_including_private_seed_exclusion",
        "source_boundary": "masked public observation, past public tool results, agent-authored memory",
        "implementation_hash": implementation_fingerprint(),
        "paid_calls_made": 0,
    }
    atomic_json(
        ROOT / "reports/verification" / f"context-preflight-{args.episode_id}-{args.decision}.json",
        preflight,
    )
    reserve = (
        limits.max_input_tokens_per_call * model.maximum_input_usd_per_million
        + limits.max_output_tokens_per_call * model.output_usd_per_million
    ) / 1_000_000
    print(
        json.dumps(
            {
                "mode": "paid_probe" if args.allow_paid else "preflight",
                "privacy_scan": preflight["privacy_scan"],
                "request_bytes": len(json.dumps(body, ensure_ascii=False).encode()),
                "source_decision": args.decision,
                "game_actions_executed": 0,
                "episode_cap_usd": limits.max_episode_cost_usd,
                "campaign_cap_usd": limits.max_batch_cost_usd,
            }
        ),
        flush=True,
    )
    if not args.allow_paid:
        policy.client.close()
        return 0
    campaign = ROOT / "private/openai-luna-smoke"
    with locked(campaign / "campaign.lock"):
        frozen = campaign / "revisions" / (args.revision + ".json")
        if frozen.exists():
            if json.loads(frozen.read_text()) != config.model_dump():
                raise ValueError("SMOKE_CAMPAIGN_CONFIG_CHANGED")
        else:
            atomic_json(frozen, config.model_dump(), immutable=True)
        spending = Spending(campaign / "spending.json", limits.max_batch_cost_usd)
        eid = store.create(
            {
                "evidence_kind": "SYNTHETIC_TEST",
                "agent": "luna",
                "config": config.public(),
                "evaluation_eligible": False,
                "validation_purpose": "recorded_context_probe",
                "source_episode_id": args.episode_id,
                "source_decision": args.decision,
                "campaign_revision": args.revision,
                "implementation_hash": implementation_fingerprint(),
            }
        )
        obs.episode_id = eid
        store.append(
            eid, "observation", obs.model_dump(mode="json"), observation_id=obs.observation_id
        )
        store.append(
            eid,
            "agent_context",
            {"context": ctx, "exchanges": exchanges},
            observation_id=obs.observation_id,
        )
        request_id = uuid.uuid4().hex
        calls, cost, valid, proposed_kind = 0, 0.0, False, None
        outcome, reason = "INFRASTRUCTURE_FAILURE", "PROBE_FAILED"
        try:
            spending.reserve(request_id, eid, reserve, limits.max_episode_cost_usd)
            calls, cost = 1, reserve
            store.append(
                eid,
                "provider_request",
                {"body": body, "reserved_usd": reserve, "attempt": 1},
                actor="agent",
                request_id=request_id,
                observation_id=obs.observation_id,
            )
            response = policy.send(body)
            cost = policy.usage_cost(response, reserve)
            spending.settle(request_id, cost)
            store.append(
                eid,
                "provider_response",
                {"body": response, "cost_usd": cost},
                actor="agent",
                request_id=request_id,
            )
            operation = Operation.validate_python(policy.parse(response))
            if operation.kind == "action":
                validate_action(operation.envelope, obs)
            store.append(
                eid,
                "agent_operation",
                {"operation": operation.model_dump(mode="json")},
                actor="agent",
                observation_id=obs.observation_id,
            )
            valid, proposed_kind = True, operation.kind
            outcome, reason = "BUDGET_EXHAUSTED", "CONTEXT_PROBE_ONE_RESPONSE_LIMIT"
        except ProviderFailure as error:
            reason = error.code
            store.append(
                eid,
                "provider_error",
                {"code": error.code, "provider_code": error.provider_code, "usage": "unknown"},
                request_id=request_id,
            )
        except BudgetExhausted as error:
            outcome, reason = "BUDGET_EXHAUSTED", str(error)
        except (ProtocolFailure, ValidationError, InvalidAction) as error:
            outcome, reason = "AGENT_PROTOCOL_FAILURE", type(error).__name__
        except Exception as error:
            reason = type(error).__name__
        finally:
            policy.client.close()
            summary = {
                "episode_id": eid,
                "evidence_kind": "SYNTHETIC_TEST",
                "outcome": outcome,
                "reason": reason,
                "attempted_actions": 0,
                "committed_actions": 0,
                "provider_calls": calls,
                "cost_usd": cost,
                "last_verified_observation_id": obs.observation_id,
            }
            store.finish(eid, summary)
        report = {
            "summary": store.summary(eid),
            "valid_operation_proposed": valid,
            "proposed_kind": proposed_kind,
            "native_action_executed": False,
            "source_episode_id": args.episode_id,
            "source_decision": args.decision,
            "implementation_hash": implementation_fingerprint(),
        }
        atomic_json(
            ROOT / "reports/verification" / f"context-probe-{eid}.json", report, immutable=True
        )
        ReviewService(store).expose(
            eid,
            "context_probe_report",
            outcome_seen=True,
            model_identity_seen=True,
            max_event_seen=len(store.events(eid)) - 1,
        )
        print(json.dumps(report, indent=2))
        return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
