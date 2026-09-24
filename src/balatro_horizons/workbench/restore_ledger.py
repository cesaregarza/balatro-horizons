"""All-attempt funding for a standalone run and its explicit restore children."""

import json
import math

from balatro_horizons.harness.terminals import GAME_TERMINAL_OUTCOMES, INCOMPLETE_TERMINAL_REASON
from balatro_horizons.storage.journal import digest
from balatro_horizons.workbench.budget_ledger import child_own_spend, validate_spending_entries


def restore_root(store, eid):
    seen = set()
    while True:
        if eid in seen:
            raise ValueError("BRANCH_ANCESTRY_CYCLE")
        seen.add(eid)
        manifest = store.manifest(eid)
        if manifest.get("batch_id"):
            raise ValueError("RESTORE_CAMPAIGN_NOT_SUPPORTED")
        parent = manifest.get("parent_episode_id")
        if not parent:
            return eid
        if manifest.get("assistance") != "restoration":
            raise ValueError("RESTORE_INTERVENTION_NOT_SUPPORTED")
        eid = parent


def _check_requests(events, entries):
    requests = [event for event in events if event["type"] == "provider_request"]
    request_ids = {event["request_id"] for event in requests}
    if len(request_ids) != len(requests):
        raise ValueError("RESTORE_LEDGER_MISMATCH")
    responses = {event["request_id"]: event["payload"]["cost_usd"]
                 for event in events if event["type"] == "provider_response"}
    for event in requests:
        row = entries.get(event["request_id"])
        if row is None or row["episode_id"] != event["episode_id"]:
            raise ValueError("RESTORE_LEDGER_MISMATCH")
        expected = (responses.get(event["request_id"]) if row["settled"]
                    else event["payload"].get("reserved_usd"))
        if expected is None or not math.isclose(row["cost"], expected, rel_tol=0, abs_tol=1e-9):
            raise ValueError("RESTORE_LEDGER_MISMATCH")
    # A crash after reserving but before journaling must not refund the reservation.
    for request_id, row in entries.items():
        if request_id not in request_ids and row["settled"]:
            raise ValueError("RESTORE_LEDGER_MISMATCH")


def _check_terminal(events, entries, manifest):
    if not events or events[-1]["type"] != "terminal":
        return
    terminal = events[-1]["payload"]
    if terminal.get("reason") == INCOMPLETE_TERMINAL_REASON:
        raise ValueError(INCOMPLETE_TERMINAL_REASON)
    prior = manifest.get("restoration", {}).get("prior_provider_calls", 0)
    calls, cost = child_own_spend(terminal, events, prior)
    # A reserve-before-journal crash may leave additional unknown charges. Never
    # refund those, but never admit a ledger that undercounts its own terminal.
    if (calls < 0 or len(entries) < calls
            or sum(row["cost"] for row in entries.values()) + 1e-9 < cost):
        raise ValueError("RESTORE_LEDGER_MISMATCH")


def restore_spending(store, parent):
    """Read one atomic ledger snapshot; preview never repairs historical records."""
    root = restore_root(store, parent)
    owners = {root}
    for item in store.list_episodes():
        manifest = item["manifest"]
        if manifest.get("restoration", {}).get("spending_owner_episode_id") == root:
            if restore_root(store, item["episode_id"]) != root:
                raise ValueError("RESTORE_LEDGER_MISMATCH")
            owners.add(item["episode_id"])
    path = store.episode_path(root, True) / "spending.json"
    entries = json.loads(path.read_text()) if path.exists() else {}
    validate_spending_entries(entries, owners=owners, allow_empty=True, error="RESTORE_LEDGER_INVALID")
    heads = {}
    for eid in sorted(owners):
        events = store.events(eid)
        heads[eid] = events[-1]["hash"] if events else "0" * 64
        if eid != parent and eid != root and (not events or events[-1]["type"] != "terminal"):
            raise ValueError("RESTORE_CONTINUATION_UNFINISHED")
        if eid != root and events and events[-1]["type"] == "terminal":
            if events[-1]["payload"].get("outcome") in GAME_TERMINAL_OUTCOMES:
                raise ValueError("RESTORE_ALREADY_FINISHED")
        owned = {key: row for key, row in entries.items() if row["episode_id"] == eid}
        _check_requests(events, owned)
        _check_terminal(events, owned, store.manifest(eid))
    return {"root": root, "path": path, "cost": sum(row["cost"] for row in entries.values()),
            "calls": len(entries), "hash": digest(entries), "heads": heads}
