#!/usr/bin/env python3
"""Preflight or run a native OpenAI smoke with explicit caps and a durable campaign ledger."""

import argparse
import fcntl
import json
import math
import os
import re
import secrets
from collections import Counter
from pathlib import Path

from balatro_horizons.agents.budget import Spending, can_afford, reservation_usd, validate_caps
from balatro_horizons.cli import doctor
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json, digest, locked


def episode_status(store, episode_id=None, *, campaign=None):
    """Report one episode's progress and record precisely that viewing exposure."""
    if episode_id is None:
        row = next(
            (
                r
                for r in store.list_episodes()
                if (
                    r["manifest"].get("smoke_campaign") == campaign
                    if campaign
                    else r["manifest"].get("validation_purpose") == "openai_luna_smoke"
                )
            ),
            None,
        )
        if row is None:
            raise ValueError("NO_PROVIDER_SMOKE_EPISODE" if campaign else "NO_LUNA_SMOKE_EPISODE")
        episode_id = row["episode_id"]
    manifest = store.manifest(episode_id)
    with locked(store.episode_path(episode_id) / ".writer.lock"):
        events = store.events(episode_id)
        summary = store.summary(episode_id)
    responses = [e for e in events if e["type"] == "provider_response"]
    settled = {e["request_id"]: e["payload"]["cost_usd"] for e in responses}
    pending = sum(
        e["payload"]["reserved_usd"]
        for e in events
        if e["type"] == "provider_request" and e["request_id"] not in settled
    )
    last = next((e["payload"] for e in reversed(events) if e["type"] == "observation"), {})
    errors = [e["payload"] for e in events if e["type"] in ("provider_error", "action_rejected")]
    result = {
        "episode_id": episode_id,
        "evidence_kind": manifest["evidence_kind"],
        "agent": manifest["agent"],
        "phase": last.get("phase", "STARTING"),
        "ante": last.get("state", {}).get("progress", {}).get("ante"),
        "committed_actions": sum(e["type"] == "action_commit" for e in events),
        "provider_calls": sum(e["type"] == "provider_request" for e in events),
        "responses_received": len(responses),
        "helper_calls_by_kind": dict(
            Counter(
                e["payload"]["operation"]["kind"] for e in events if e["type"] == "helper_result"
            )
        ),
        "settled_cost_usd": sum(settled.values()),
        "reserved_unknown_usd": pending,
        "outcome": summary.get("outcome") if summary else None,
        "reason": summary.get("reason") if summary else None,
        "last_error": {k: errors[-1].get(k) for k in ("code", "provider_code")} if errors else None,
    }
    ReviewService(store).expose(
        episode_id,
        "smoke_status",
        outcome_seen=bool(summary),
        model_identity_seen=True,
        max_event_seen=events[-1]["sequence"] if events else -1,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=ROOT / "configs/luna-smoke.yaml")
    parser.add_argument("--agent", default="luna", help="Exact configured model key")
    parser.add_argument("--campaign", default="openai-luna-smoke", help="Private ledger name")
    parser.add_argument("--authorized-episode-cap", type=float, default=1)
    parser.add_argument("--authorized-total-cap", type=float, default=5)
    parser.add_argument(
        "--env-file", type=Path, help="Read OPENAI_API_KEY only; never execute file contents"
    )
    parser.add_argument(
        "--revision",
        help="Record a configuration revision while retaining the same campaign ledger",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--status", action="store_true", help="Show progress; records review exposure"
    )
    parser.add_argument(
        "--episode-id", help="Status for a specific episode; defaults to latest smoke"
    )
    mode.add_argument("--allow-paid", action="store_true", help="Run one native episode")
    mode.add_argument("--dry-run", action="store_true", help="Check readiness without API calls")
    parser.add_argument(
        "--transport-only",
        action="store_true",
        help="Exercise one action through a synthetic game; never native benchmark evidence",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{1,64}", args.campaign):
        parser.error("campaign must contain 1-64 lowercase letters, digits or hyphens")
    if any(
        not math.isfinite(v) or v <= 0
        for v in (args.authorized_episode_cap, args.authorized_total_cap)
    ):
        parser.error("authorized caps must be positive finite USD amounts")
    if args.revision and not re.fullmatch(r"[a-z0-9-]{1,40}", args.revision):
        parser.error("revision must contain 1-40 lowercase letters, digits or hyphens")
    if args.status:
        print(
            json.dumps(
                episode_status(
                    Store(ROOT / "data"),
                    args.episode_id,
                    campaign=args.campaign if args.campaign != "openai-luna-smoke" else None,
                ),
                indent=2,
            )
        )
        return 0
    if args.episode_id:
        parser.error("--episode-id requires --status")
    config = load_config(args.config)
    if set(config.models) != {args.agent}:
        raise ValueError("ONE_EXPLICIT_SMOKE_MODEL_REQUIRED")
    limits = config.budgets
    if (
        not limits.max_episode_cost_usd
        or limits.max_episode_cost_usd > args.authorized_episode_cap
        or not limits.max_batch_cost_usd
        or limits.max_batch_cost_usd > args.authorized_total_cap
    ):
        raise ValueError("SMOKE_EXCEEDS_AUTHORIZED_CAPS")
    config.budgets.paid_calls_enabled = bool(args.allow_paid)
    model = config.models[args.agent]
    if model.provider != "openai":
        raise ValueError("OPENAI_PROVIDER_REQUIRED")
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name == "OPENAI_API_KEY":
                os.environ[name] = value.strip().strip("\"'")
    native = (
        {"blockers": [], "native_certification": "not_used_synthetic_transport_test"}
        if args.transport_only
        else doctor(config)
    )
    blockers = list(native["blockers"])
    if not os.environ.get("OPENAI_API_KEY"):
        blockers.append("MISSING_PROVIDER_CREDENTIAL")
    campaign = ROOT / "private" / args.campaign
    ledger = campaign / "spending.json"
    validate_caps(limits.max_episode_cost_usd, limits.max_batch_cost_usd)
    reserve = reservation_usd(model, limits)
    affordable, cost_context = Spending(ledger, limits.max_batch_cost_usd).affordability(reserve)
    total = cost_context["campaign_committed_usd"]
    if not can_afford(0, reserve, limits.max_episode_cost_usd):
        blockers.append("EPISODE_CAP_BELOW_RESERVATION")
    elif not affordable:
        blockers.append("CAMPAIGN_COST_CAP")
    check = {
        "mode": "paid_smoke" if args.allow_paid else "preflight",
        "evidence_kind": "SYNTHETIC_TEST" if args.transport_only else "NATIVE",
        "model": model.model,
        "settings": model.settings,
        "deck": config.environment.deck,
        "stake": config.environment.stake,
        "episode_cap_usd": limits.max_episode_cost_usd,
        "campaign_cap_usd": limits.max_batch_cost_usd,
        "campaign_accounted_usd": total,
        "per_request_reserve_usd": reserve,
        "credential_present": bool(os.environ.get("OPENAI_API_KEY")),
        "native_certification": native["native_certification"],
        "blockers": blockers,
        "paid_calls_made": 0,
    }
    print(json.dumps(check, indent=2), flush=True)
    if blockers or not args.allow_paid:
        return 1 if blockers else 0
    campaign.mkdir(parents=True, exist_ok=True)
    # Serialize every invocation, including the durable shared spending ledger.
    with (campaign / "campaign.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = (
            campaign / "revisions" / (args.revision + ".json")
            if args.revision
            else campaign / "config.json"
        )
        if frozen.exists():
            if json.loads(frozen.read_text()) != config.model_dump():
                raise ValueError("SMOKE_CAMPAIGN_CONFIG_CHANGED")
        else:
            atomic_json(frozen, config.model_dump(), immutable=True)
        store = Store(ROOT / "data")
        service = RunService(store, ReviewService(store))
        seed = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        run_config = config.model_copy(deep=True)
        if args.transport_only:
            run_config.budgets.max_game_actions = 1
        summary = service.execute(
            run_config,
            args.agent,
            seed,
            offline=args.transport_only,
            spending=Spending(ledger, limits.max_batch_cost_usd),
            extra={
                "evaluation_eligible": False,
                "validation_purpose": "openai_luna_smoke"
                if args.campaign == "openai-luna-smoke"
                else "openai_provider_smoke",
                "smoke_campaign": args.campaign,
                "campaign_revision": args.revision or "original",
            },
        )
        events = store.events(summary["episode_id"])
        responses = [e["payload"]["body"] for e in events if e["type"] == "provider_response"]
        result = {
            "summary": summary,
            "config_hash": digest(run_config.model_dump()),
            "responses_received": len(responses),
            "provider_errors": [
                {"code": e["payload"]["code"], "provider_code": e["payload"].get("provider_code")}
                for e in events
                if e["type"] == "provider_error"
            ],
            "responses_with_reasoning_summary": sum(
                any(
                    item.get("type") == "reasoning" and item.get("summary")
                    for item in response.get("output", [])
                )
                for response in responses
                if isinstance(response, dict)
            ),
            "evaluation_eligible": False,
        }
        atomic_json(
            ROOT / "reports/verification" / f"provider-smoke-{summary['episode_id']}.json",
            result,
            immutable=True,
        )
        print(json.dumps(result, indent=2))
        if args.transport_only:
            return (
                0
                if (summary["committed_actions"] == 1 and summary["reason"] == "GAME_ACTION_LIMIT")
                else 1
            )
        return 0 if summary["outcome"] in ("WIN", "GAME_LOSS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
