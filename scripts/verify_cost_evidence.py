#!/usr/bin/env python3
"""Check serialized costs and reconstructed receipts on completed native public journals.

No Windows access, game launch, provider transport, or private state is used.
Evaluator fixtures may omit last_action; reconstructed receipts are counted separately.
"""

import argparse
import json
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.transport import context_payload
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.storage.journal import Store, atomic_json


def verify(store, eid):
    events = store.events(eid)
    observations = {
        e["observation_id"]: Observation.model_validate(e["payload"])
        for e in events
        if e["type"] == "observation"
    }
    receipts = {}
    kinds = set()
    persisted = 0
    for event in events:
        if event["type"] != "action_commit":
            continue
        envelope = ActionEnvelope.model_validate(event["payload"])
        decision = envelope.observation_id
        before, after = observations[decision], observations[decision + 1]
        receipt = last_action(before, after, envelope.action)
        if receipt.transaction is None:
            continue
        kinds.add(envelope.action.type)
        if after.last_action is not None:
            assert after.last_action.transaction == receipt.transaction
            persisted += 1
        receipts[decision + 1] = receipt
    checks = 0
    for decision, observation in observations.items():
        observation = observation.model_copy(deep=True)
        if decision in receipts:
            observation.last_action = receipts[decision]
        ctx, exchanges = decision_context(observation, [])
        for provider in ("openai", "anthropic"):
            body = context_payload(ctx, exchanges, provider)
            messages = body["input" if provider == "openai" else "messages"]
            content = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
            costs = content["current_costs"]
            assert costs["observation_id"] == decision
            assert costs["cash_balance"] == observation.state.resources.money
            for offer in costs.get("offers", []):
                source = next(o for o in observation.state.offers if o.id == offer["offer_id"])
                assert offer["cash_cost"] == source.price
            for name, quote in costs.get("rerolls", {}).items():
                field = "shop_reroll_cost" if name == "reroll_shop" else "boss_reroll_cost"
                assert quote["cash_cost"] == getattr(observation.state.resources, field)
            if decision in receipts:
                expected = receipts[decision].transaction.model_dump(mode="json")
                assert content["observation"]["last_action"]["transaction"] == expected
                assert expected["actual_cash_charge"] is None
            checks += 1
    assert checks and receipts
    return {
        "observations": len(observations),
        "provider_contexts_checked": checks,
        "transaction_transitions": len(receipts),
        "persisted_receipts_checked": persisted,
        "reconstructed_receipts_checked": len(receipts) - persisted,
        "transaction_types": sorted(kinds),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.data_dir)
    manifest, summary = store.manifest(args.episode_id), store.summary(args.episode_id)
    if manifest["evidence_kind"] != "NATIVE" or summary is None:
        parser.error("a completed native episode is required")
    result = {
        "status": "passed",
        "source_episode_id": args.episode_id,
        "source_journal_head": summary["journal_head"],
        "evidence_kind": "NATIVE_CALIBRATION",
        "implementation_hash": implementation_fingerprint(),
        "provider_calls": 0,
        "additional_game_launches": 0,
        "checks": verify(store, args.episode_id),
    }
    atomic_json(args.report, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
