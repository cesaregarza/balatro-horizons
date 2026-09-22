"""Validation and ordering for the immutable budget-extension ledger."""

import math

from balatro_horizons.storage.journal import digest


def _matching_spend(rows, calls, cost):
    return (
        type(calls) is int
        and calls >= 0
        and type(cost) in (int, float)
        and math.isfinite(cost)
        and cost >= 0
        and len(rows) == calls
        and math.isclose(sum(row["cost"] for row in rows), cost, rel_tol=0, abs_tol=1e-9)
    )


def _validate_rows(entries):
    if not isinstance(entries, dict) or not entries:
        raise ValueError("BUDGET_EXTENSION_INVALID_LEDGER")
    for row in entries.values():
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("episode_id"), str)
            or type(row.get("settled")) is not bool
            or type(row.get("cost")) not in (int, float)
            or not math.isfinite(row["cost"])
            or row["cost"] < 0
        ):
            raise ValueError("BUDGET_EXTENSION_INVALID_LEDGER")


def _reconcile_parent(parent_id, terminal, entries):
    parent_rows = [row for row in entries.values() if row["episode_id"] == parent_id]
    if not _matching_spend(parent_rows, terminal["provider_calls"], terminal["cost_usd"]):
        raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")


def _ordered_children(store, parent_id, entries):
    """Use admission hashes, resolving zero-spend ties before spending children."""
    accounted = {parent_id}
    # SQLite row order can change when an episode is reindexed. Reconstruct the
    # admission chain from each immutable child's preceding-ledger hash instead.
    pending = [item for item in store.list_episodes()
               if item["manifest"].get("parent_episode_id") == parent_id
               and item["manifest"].get("assistance") == "budget_extension"]
    spending_owners = {row["episode_id"] for row in entries.values()}
    while pending:
        preceding = {key: row for key, row in entries.items()
                     if row["episode_id"] in accounted}
        prior_hash = digest(preceding)
        matches = [item for item in pending if (
            item["manifest"].get("budget_extension", {}).get("ledger_hash_at_admission") == prior_hash
            and _matching_spend(
                list(preceding.values()),
                item["manifest"]["budget_extension"].get("prior_provider_calls"),
                item["manifest"]["budget_extension"].get("prior_cost_usd"),
            )
        )]
        if not matches:
            raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")
        # A child with no spending leaves the same admission hash for the next
        # child. Account for all such children before consuming a spending child.
        match = next((item for item in matches
                      if item["episode_id"] not in spending_owners), matches[0])
        pending.remove(match)
        yield match
        accounted.add(match["episode_id"])

    if any(row["episode_id"] not in accounted for row in entries.values()):
        raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")


def _child_own_spend(child, events, prior_calls):
    """Runner counts inherited calls; pre-run failure and recovery count own calls.

    All three terminal producers record only the child's own cost, including
    retained reservations whose usage is unknown.
    """
    started = any(event["type"] == "episode_start" for event in events)
    recovered = (
        child.get("outcome") == "INFRASTRUCTURE_FAILURE"
        and child.get("reason") in ("PROCESS_INTERRUPTED", "AMBIGUOUS_ACTION_AFTER_CRASH")
    )
    calls = (child["provider_calls"] if not started or recovered
             else child["provider_calls"] - prior_calls)
    if not started and calls != 0:
        raise ValueError("BUDGET_EXTENSION_CHILD_SPEND_MISMATCH")
    return calls, child["cost_usd"]


def reconcile_shared_ledger(store, parent_id, terminal, entries):
    """Match one locked ledger snapshot to every terminal that spent from it."""
    _validate_rows(entries)
    _reconcile_parent(parent_id, terminal, entries)
    for item in _ordered_children(store, parent_id, entries):
        manifest = item["manifest"]
        child_id = item["episode_id"]
        extension = manifest.get("budget_extension") or {}
        prior_calls = extension["prior_provider_calls"]
        if extension.get("spending_owner_episode_id") != parent_id:
            raise ValueError("BUDGET_EXTENSION_LEDGER_MISMATCH")
        child = store.summary(child_id)
        if child is None:
            raise ValueError("BUDGET_EXTENSION_CHILD_UNRESOLVED")
        child_rows = [row for row in entries.values() if row["episode_id"] == child_id]
        child_calls, child_cost = _child_own_spend(child, store.events(child_id), prior_calls)
        if not _matching_spend(child_rows, child_calls, child_cost):
            raise ValueError("BUDGET_EXTENSION_CHILD_SPEND_MISMATCH")
    return sum(row["cost"] for row in entries.values()), len(entries)
