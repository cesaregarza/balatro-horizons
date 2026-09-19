#!/usr/bin/env python3
"""Group one recorded public run into rounds, purchases and an action ledger.

No game or provider is contacted. Reading a whole run records review exposure.
"""

from collections import Counter
from datetime import datetime
from decimal import Decimal

from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import locked


def summary_input(store, eid):
    """Project only report inputs; opaque provider output is never an export input."""
    with locked(store.episode_path(eid) / ".writer.lock"):
        records = store.events(eid)
        manifest = store.manifest(eid)
        summary = store.summary(eid)
    events = []
    for event in records:
        kind, payload = event["type"], event["payload"]
        if kind == "observation":
            payload = Observation.model_validate(payload).model_dump(mode="json")
        elif kind in ("action_commit", "action_intent"):
            payload = ActionEnvelope.model_validate(payload).model_dump(mode="json")
        elif kind == "provider_request":
            payload = {
                "body": {
                    "tools": [
                        {"name": tool.get("name")} for tool in payload["body"].get("tools", [])
                    ]
                }
            }
        elif kind == "provider_response":
            body = payload.get("body")
            usage = body.get("usage") if isinstance(body, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            payload = {
                "body": {
                    "usage": {
                        key: usage[key] for key in ("input_tokens", "output_tokens") if key in usage
                    }
                }
            }
        elif kind == "helper_result":
            payload = {"operation": payload["operation"]}
        elif kind == "action_rejected":
            payload = {"code": payload["code"]}
        elif kind == "agent_context":
            # A decision may be waiting on helpers before any gameplay intent.
            # The ledger needs only its presence, never the large context body.
            payload = {}
        else:
            continue
        events.append(
            {
                **{
                    key: event.get(key)
                    for key in (
                        "event_id",
                        "sequence",
                        "type",
                        "observation_id",
                        "request_id",
                        "actor",
                    )
                },
                "payload": payload,
            }
        )
    public = {
        "manifest": {
            key: manifest[key]
            for key in (
                "schema_version",
                "episode_id",
                "created_at",
                "evidence_kind",
                "agent",
                "config",
                "evaluation_eligible",
                "fixture",
                "validation_purpose",
                "parent_episode_id",
                "parent_decision",
                "assistance",
                "batch_id",
                "slot_id",
            )
            if key in manifest
        },
        "summary": summary,
        "events": events,
        "journal_head": records[-1]["hash"] if records else None,
        "last_timestamp": records[-1]["timestamp"] if records else manifest["created_at"],
        "omitted_content": [
            "provider instructions and input bodies",
            "provider output and opaque content",
            "helper result bodies",
            "agent context snapshots",
            "annotations",
        ],
    }
    scan(public, [store.manifest(eid, True).get("seed")])
    ReviewService(store).expose(
        eid,
        "decision_summary",
        outcome_seen=bool(summary),
        model_identity_seen=True,
        max_event_seen=records[-1]["sequence"] if records else -1,
    )
    return public


def difference(after, before):
    if after is None or before is None:
        return None
    return str(Decimal(str(after)) - Decimal(str(before)))


def played_counts(state):
    return {
        name: int(text.rsplit("played: ", 1)[1])
        for name, text in state["hand_levels"].items()
        if "played: " in text
    }


def label(card):
    if card.get("rank") and card.get("suit"):
        return f"{card['rank']} of {card['suit']}"
    return card.get("label", "Unknown object")


def action_details(action, state):
    objects = {
        obj["id"]: obj
        for area in ("hand", "jokers", "consumables", "offers", "revealed_blinds")
        for obj in state[area]
    }
    result = {}
    for key in ("blind_id", "offer_id", "owned_id", "consumable_id"):
        if key in action:
            obj = objects.get(action[key], {})
            result.update(item=label(obj), effects=obj.get("effects", []))
            if key == "offer_id":
                result.update(item_kind=obj.get("kind"), listed_price=obj.get("price"))
    for key in ("card_ids", "target_ids"):
        if key in action:
            result[key.replace("_ids", "s")] = [
                label(objects.get(handle, {})) for handle in action[key]
            ]
    if action["type"] == "buy":
        result["mode"] = action.get("mode", "acquire")
    if action["type"] == "reorder":
        result["area"] = action["area"]
        result["ordering_before"] = [label(obj) for obj in state[action["area"]]]
        result["ordered_objects"] = [
            label(objects.get(handle, {})) for handle in action["ordered_ids"]
        ]
    return result


def uncommitted_actions(events, observations, *, settling=(), live=False):
    committed = {
        e["request_id"]
        for e in events
        if e["type"] == "action_commit" and e.get("request_id") and e["request_id"] not in settling
    }
    rejected = {
        e["request_id"]: e["payload"]["code"]
        for e in events
        if e["type"] == "action_rejected" and e.get("request_id")
    }
    rows = []
    for event in events:
        if event["type"] != "action_intent" or event.get("request_id") in committed:
            continue
        envelope = event["payload"]
        before = observations[envelope["observation_id"]]
        code = rejected.get(event.get("request_id"))
        rows.append(
            {
                "decision": envelope["observation_id"],
                "event_id": event["event_id"],
                "ante": before["state"]["progress"]["ante"],
                "phase": before["phase"],
                "type": envelope["action"]["type"],
                "note": envelope.get("decision_note"),
                "status": "rejected"
                if code
                else "awaiting_transition"
                if event.get("request_id") in settling
                else "in_progress"
                if live
                else "no_committed_transition",
                "rejection_code": code,
                **action_details(envelope["action"], before["state"]),
            }
        )
    return rows


def summarize(public):
    events = public["events"]
    observations = [e for e in events if e["type"] == "observation"]
    if not observations:
        return {"manifest": public["manifest"], "summary": public["summary"], "actions": []}
    by_id = {e["observation_id"]: e["payload"] for e in observations}
    ledger, rounds, skipped, purchases = [], [], [], []
    settling = set()
    current_round = None
    counts = Counter()
    for event in events:
        if event["type"] != "action_commit":
            continue
        envelope = event["payload"]
        action = envelope["action"]
        before = by_id[envelope["observation_id"]]
        after_event = next((o for o in observations if o["sequence"] > event["sequence"]), None)
        if after_event is None:
            if public["summary"] is None:
                # A live reader can see a durable commit before the runner records
                # the settled observation. Preserve the pending row, not an outcome.
                settling.add(event.get("request_id"))
                continue
            raise ValueError("MISSING_SETTLED_OBSERVATION")
        after = after_event["payload"]
        state, settled = before["state"], after["state"]
        old_resources, resources = state["resources"], settled["resources"]
        kind = action["type"]
        counts[kind] += 1
        row = {
            "action_number": len(ledger) + 1,
            "decision": envelope["observation_id"],
            "event_id": event["event_id"],
            "ante": state["progress"]["ante"],
            "blind": state["progress"]["blind"],
            "phase": before["phase"],
            "type": kind,
            "money_before": old_resources["money"],
            "money_after": resources["money"],
            "money_change": difference(resources["money"], old_resources["money"]),
            "note": envelope.get("decision_note"),
            "target_before": old_resources["target"],
            "target_after": resources["target"],
            "hands_after": resources["hands"],
            "discards_after": resources["discards"],
            "jokers_after": [label(obj) for obj in settled["jokers"]],
            "jokers_added": list(
                (
                    Counter(label(obj) for obj in settled["jokers"])
                    - Counter(label(obj) for obj in state["jokers"])
                ).elements()
            ),
            "jokers_removed": list(
                (
                    Counter(label(obj) for obj in state["jokers"])
                    - Counter(label(obj) for obj in settled["jokers"])
                ).elements()
            ),
        }
        row.update(action_details(action, state))
        if kind == "select_blind":
            current_round = {
                "ante": row["ante"],
                "blind": row["item"],
                "target": resources["target"],
                "start_action": row["action_number"],
                "hands_played": 0,
                "discards_used": 0,
                "scores": [],
                "hand_types": [],
                "cleared": False,
            }
            rounds.append(current_round)
        elif kind == "skip_blind":
            skipped.append(row)
        elif kind == "play_hand":
            before_counts, after_counts = played_counts(state), played_counts(settled)
            hand_types = [
                name for name, count in after_counts.items() if count > before_counts.get(name, 0)
            ]
            row.update(
                hand_types=hand_types,
                score=difference(resources["chips"], old_resources["chips"]),
                total_chips=resources["chips"],
            )
            if current_round is not None:
                current_round["hands_played"] += 1
                current_round["scores"].append(row["score"])
                current_round["hand_types"].extend(hand_types)
                current_round.update(
                    total_chips=resources["chips"],
                    hands_remaining=resources["hands"],
                    discards_remaining=resources["discards"],
                )
                current_round["cleared"] = after["phase"] == "ROUND_EVAL"
        elif kind == "discard" and current_round is not None:
            current_round["discards_used"] += 1
        elif kind == "cash_out" and current_round is not None:
            current_round.update(
                end_action=row["action_number"],
                cash_out_balance_change=row["money_change"],
                shop_balance=resources["money"],
            )
            current_round = None
        if kind in ("buy", "sell", "choose_pack", "use_consumable"):
            purchases.append(row)
        ledger.append(row)
    last = observations[-1]["payload"]
    summary = public["summary"] or {}
    requests = [e for e in events if e["type"] == "provider_request"]
    responses = [e for e in events if e["type"] == "provider_response"]
    usages = [e["payload"]["body"].get("usage", {}) for e in responses]
    result = {
        "manifest": public["manifest"],
        "summary": summary,
        "ledger_action_count": len(ledger),
        "action_counts": dict(counts),
        "rounds": rounds,
        "skips": skipped,
        "purchases_and_changes": purchases,
        "final": {"phase": last["phase"], **last["state"]},
        "money_range": {
            "min": str(
                min(Decimal(o["payload"]["state"]["resources"]["money"]) for o in observations)
            ),
            "max": str(
                max(Decimal(o["payload"]["state"]["resources"]["money"]) for o in observations)
            ),
        },
        "helper_calls": [e["payload"]["operation"] for e in events if e["type"] == "helper_result"],
        "rejections": [
            {"decision": e["observation_id"], "code": e["payload"]["code"]}
            for e in events
            if e["type"] == "action_rejected"
        ],
        "provider_input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "provider_output_tokens": sum(u.get("output_tokens", 0) for u in usages),
        "offered_tools": [t.get("name") for t in requests[0]["payload"]["body"].get("tools", [])]
        if requests
        else [],
        "memory_updates": sum(
            bool(e["payload"].get("memory_update")) for e in events if e["type"] == "action_commit"
        ),
        "actions": ledger,
        "uncommitted_actions": uncommitted_actions(
            events, by_id, settling=settling, live=public["summary"] is None
        ),
        "pending_decisions": pending_decisions(events, by_id, public["summary"] is None),
    }
    if summary and len(ledger) != summary.get("committed_actions"):
        raise ValueError("ACTION_TOTAL_MISMATCH")
    scan(result)
    return result


def pending_decisions(events, observations, live):
    """Navigation only: never count helper-only decisions as game actions."""
    intents = {e["observation_id"] for e in events if e["type"] in ("action_intent", "action_commit")}
    started = {e["observation_id"] for e in events if e["type"] in ("agent_context", "provider_request")}
    return [
        {"event_id": event["event_id"], "decision": event["observation_id"],
         "ante": event["payload"]["state"]["progress"]["ante"], "phase": event["payload"]["phase"],
         "type": "model_turn", "note": None, "status": "awaiting_model" if live else "no_game_action"}
        for event in events if event["type"] == "observation"
        and event["observation_id"] in started - intents
        and event["observation_id"] in observations
    ]


def build_summary(store, eid):
    """Build a retrospective ledger with verified public provenance."""
    public = summary_input(store, eid)
    result = summarize(public)
    started = datetime.fromisoformat(public["manifest"]["created_at"])
    ended = datetime.fromisoformat(public["last_timestamp"])
    result["recorded_duration_seconds"] = (ended - started).total_seconds()
    result["source_journal_head"] = public["journal_head"]
    result["omitted_content"] = public["omitted_content"]
    return result
