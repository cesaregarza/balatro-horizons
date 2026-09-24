#!/usr/bin/env python3
"""Group one recorded public run into rounds, purchases and an action ledger.

No game or provider is contacted. Reading a whole run records review exposure.
"""

import json
from collections import Counter
from datetime import datetime
from decimal import Decimal

from balatro_horizons.evaluation.reports import scan
from balatro_horizons.harness.context.freeze import FROZEN_INTERFACE
from balatro_horizons.review import action_accounting as action_totals
from balatro_horizons.review.service import ReviewService
from balatro_horizons.review.spend import run_spend
from balatro_horizons.review.summary_projection import event_projection, manifest_projection
from balatro_horizons.storage.journal import digest, locked


def recorded_protocol_interface(store, records):
    """Read only the immutable recorded label, never an executable old protocol."""
    reference = next(
        (event["payload"].get("agent_protocol") for event in records
         if event["type"] == "episode_start"),
        None,
    )
    if not isinstance(reference, dict):
        return None
    try:
        path = store.episode_path(reference["episode_id"], True) / "agent-protocol.json"
        bundle = json.loads(path.read_text())
        interface = bundle.get("interface")
        if (digest(bundle) == reference.get("hash") and isinstance(interface, str)
                and 0 < len(interface) <= 64 and interface.isprintable()):
            return interface
    except (KeyError, OSError, TypeError, ValueError):
        pass
    return None


def summary_input(store, eid):
    """Project only report inputs; opaque provider output is never an export input."""
    with locked(store.episode_path(eid) / ".writer.lock"):
        records = store.events(eid)
        manifest = store.manifest(eid)
        summary = store.summary(eid)
    events = event_projection(records)
    public = {
        "manifest": manifest_projection(manifest),
        "summary": summary,
        "spend": run_spend(records, summary, restoration=manifest.get("restoration")),
        "action_accounting": action_totals.action_accounting(store, eid, records, summary),
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
    recorded_interface = recorded_protocol_interface(store, records)
    public["manifest"]["recorded_interface"] = recorded_interface
    public["manifest"]["current_harness"] = recorded_interface == FROZEN_INTERFACE
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


def _action_objects(state):
    return {
        obj["id"]: obj
        for area in ("hand", "jokers", "consumables", "offers", "revealed_blinds")
        for obj in state[area]
    }


def _common_action_details(action, objects):
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
    return result


def _buy_details(action, _state, _objects):
    return {"mode": action.get("mode", "acquire")}


def _reorder_details(action, state, objects):
    return {
        "area": action["area"],
        "ordering_before": [label(obj) for obj in state[action["area"]]],
        "ordered_objects": [label(objects.get(handle, {})) for handle in action["ordered_ids"]],
    }


def _no_action_details(_action, _state, _objects):
    return {}


ACTION_DETAIL_HANDLERS = {
    action_type: _no_action_details
    for action_type in (
        "select_blind", "skip_blind", "play_hand", "discard", "sell", "use_consumable",
        "reroll_shop", "reroll_boss", "choose_pack", "skip_pack", "leave_shop", "cash_out",
    )
}
ACTION_DETAIL_HANDLERS.update({"buy": _buy_details, "reorder": _reorder_details})


def action_details(action, state):
    objects = _action_objects(state)
    details = _common_action_details(action, objects)
    handler = ACTION_DETAIL_HANDLERS[action["type"]]
    details.update(handler(action, state, objects))
    return details


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


def joker_changes(before, after):
    before_names = Counter(label(obj) for obj in before["jokers"])
    after_names = Counter(label(obj) for obj in after["jokers"])
    return {
        "jokers_after": list(after_names.elements()),
        "jokers_added": list((after_names - before_names).elements()),
        "jokers_removed": list((before_names - after_names).elements()),
    }


def update_round_bookkeeping(rounds, current, kind, row, before, after, after_phase, resources, old_resources):
    if kind == "select_blind":
        current = {
            "ante": row["ante"], "blind": row["item"], "target": resources["target"],
            "start_action": row["action_number"], "hands_played": 0, "discards_used": 0,
            "scores": [], "hand_types": [], "cleared": False,
        }
        rounds.append(current)
    elif kind == "play_hand":
        before_counts, after_counts = played_counts(before), played_counts(after)
        hand_types = [name for name, count in after_counts.items() if count > before_counts.get(name, 0)]
        row.update(
            hand_types=hand_types,
            score=difference(resources["chips"], old_resources["chips"]),
            total_chips=resources["chips"],
        )
        if current is not None:
            current["hands_played"] += 1
            current["scores"].append(row["score"])
            current["hand_types"].extend(hand_types)
            current.update(total_chips=resources["chips"], hands_remaining=resources["hands"], discards_remaining=resources["discards"])
            current["cleared"] = after_phase == "ROUND_EVAL"
    elif kind == "discard" and current is not None:
        current["discards_used"] += 1
    elif kind == "cash_out" and current is not None:
        current.update(end_action=row["action_number"], cash_out_balance_change=row["money_change"], shop_balance=resources["money"])
        current = None
    return current


def aggregate_purchase(purchases, kind, row):
    if kind in ("buy", "sell", "choose_pack", "use_consumable"):
        purchases.append(row)


def token_cost_accounting(events):
    requests = [event for event in events if event["type"] == "provider_request"]
    responses = [event for event in events if event["type"] == "provider_response"]
    usages = [event["payload"]["body"].get("usage", {}) for event in responses]
    return {
        "provider_input_tokens": sum(usage.get("input_tokens", 0) for usage in usages),
        "provider_output_tokens": sum(usage.get("output_tokens", 0) for usage in usages),
        "offered_tools": [tool.get("name") for tool in requests[0]["payload"]["body"].get("tools", [])]
        if requests else [],
        "memory_updates": sum(bool(event["payload"].get("memory_update")) for event in events if event["type"] == "action_commit"),
    }


def reconcile_uncommitted(events, observations, settling, live):
    return uncommitted_actions(events, observations, settling=settling, live=live)


def _committed_rows(public, observations, by_id):
    events = public["events"]
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
            **joker_changes(state, settled),
        }
        row.update(action_details(action, state))
        if kind == "skip_blind":
            skipped.append(row)
        current_round = update_round_bookkeeping(rounds, current_round, kind, row, state, settled, after["phase"], resources, old_resources)
        aggregate_purchase(purchases, kind, row)
        ledger.append(row)
    return ledger, rounds, skipped, purchases, settling, counts


def summarize(public):
    events = public["events"]
    observations = [e for e in events if e["type"] == "observation"]
    if not observations:
        return action_totals.empty_summary(public)
    by_id = {e["observation_id"]: e["payload"] for e in observations}
    ledger, rounds, skipped, purchases, settling, counts = _committed_rows(public, observations, by_id)
    last = observations[-1]["payload"]
    summary = public["summary"] or {}
    accounting = token_cost_accounting(events)
    result = {
        "manifest": public["manifest"],
        "summary": summary,
        "ledger_action_count": len(ledger),
        "action_accounting": public["action_accounting"],
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
        **accounting,
        "actions": ledger,
        "uncommitted_actions": reconcile_uncommitted(events, by_id, settling, public["summary"] is None),
        "pending_decisions": pending_decisions(events, by_id, public["summary"] is None),
    }
    action_totals.assert_action_total(public, ledger)
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
    result["spend"] = public["spend"]
    started = datetime.fromisoformat(public["manifest"]["created_at"])
    ended = datetime.fromisoformat(public["last_timestamp"])
    result["recorded_duration_seconds"] = (ended - started).total_seconds()
    result["source_journal_head"] = public["journal_head"]
    result["omitted_content"] = public["omitted_content"]
    return result
