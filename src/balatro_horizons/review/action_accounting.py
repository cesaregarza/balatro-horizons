"""Reconcile episode-only review rows against immutable lineage counters."""

from balatro_horizons.workbench.branches import inherited_events

RECOVERY_REASONS = {"PROCESS_INTERRUPTED", "AMBIGUOUS_ACTION_AFTER_CRASH"}


def action_accounting(store, eid, records, summary):
    """Count only verified public commits, not decision IDs or private saves."""
    own = sum(event["type"] == "action_commit" for event in records)
    inherited = sum(event["type"] == "action_commit" for event in inherited_events(store, eid))
    # Runner terminals include the restored prefix. Recovery and pre-run
    # failures count only this journal; never infer that distinction from a delta.
    started = any(event["type"] == "episode_start" for event in records)
    recovered = (summary or {}).get("reason") in RECOVERY_REASONS
    return {
        "ledger_scope": "episode",
        "own_committed_actions": own,
        "inherited_committed_actions": inherited,
        "total_committed_actions": own + inherited,
        "terminal_count_scope": "lineage" if started and not recovered else "episode",
    }


def assert_action_total(public, ledger):
    summary, counts = public["summary"], public["action_accounting"]
    if not summary:
        return  # A live commit may still await its settled observation.
    expected = counts[
        "total_committed_actions" if counts["terminal_count_scope"] == "lineage"
        else "own_committed_actions"
    ]
    if (len(ledger) != counts["own_committed_actions"]
            or summary.get("committed_actions") != expected):
        raise ValueError("ACTION_TOTAL_MISMATCH")


def empty_summary(public):
    assert_action_total(public, [])
    return {
        "manifest": public["manifest"], "summary": public["summary"], "actions": [],
        "ledger_action_count": 0, "action_accounting": public["action_accounting"],
    }
