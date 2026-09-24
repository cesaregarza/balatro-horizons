"""Compact operator snapshots; unchanged journals are verified only once.

This cache belongs to the operator view, never to prospective review or execution.
It retains aggregates, not observations, provider bodies, or engine state.
"""

from threading import Lock

from balatro_horizons.review.spend import run_spend
from balatro_horizons.storage.journal import locked


def journal_stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def aggregate(events, *, restoration=None):
    summary = next((e["payload"] for e in reversed(events) if e["type"] == "terminal"), None)
    spend = run_spend(events, summary, restoration=restoration)
    progress = None
    if summary is None:
        last = next((e["payload"] for e in reversed(events) if e["type"] == "observation"), None)
        progress = {
            "committed_actions": sum(e["type"] == "action_commit" for e in events),
            "provider_calls": sum(e["type"] == "provider_request" for e in events),
            "cost_usd": spend["accounted_usd"],
            "phase": last["phase"] if last else "STARTING",
            "ante": last["state"]["progress"]["ante"] if last else None,
        }
    return {"summary": summary, "progress": progress, "spend": spend}, len(events) - 1


class OperatorStatus:
    def __init__(self, store, review):
        self.store, self.review = store, review
        self._cache = {}
        self._exposed = {}
        # Multiple tabs share one refresh; they cannot all reparse the same journal.
        self._lock = Lock()

    def episodes(self):
        with self._lock:
            result = []
            for row in self.store.list_episodes():
                eid = row["episode_id"]
                directory = self.store.episode_path(eid)
                path = directory / "events.jsonl"
                stamp = journal_stamp(path)
                cached = self._cache.get(eid)
                if cached is None or cached[0] != stamp:
                    # A concurrent append must not look like a torn/corrupt journal.
                    with locked(directory / ".writer.lock"):
                        stamp = journal_stamp(path)
                        data, sequence = aggregate(
                            self.store.events(eid), restoration=row["manifest"].get("restoration"),
                        )
                    cached = (stamp, data, sequence)
                    self._cache[eid] = cached
                _, data, sequence = cached
                exposure = (sequence, data["summary"] is not None)
                if self._exposed.get(eid) != exposure:
                    self.review.expose(
                        eid,
                        "operator_status",
                        outcome_seen=exposure[1],
                        model_identity_seen=True,
                        max_event_seen=sequence,
                    )
                    self._exposed[eid] = exposure
                result.append({"episode_id": eid, "agent": row["manifest"]["agent"], **data})
            present = {row["episode_id"] for row in result}
            for eid in self._cache.keys() - present:
                del self._cache[eid]
                self._exposed.pop(eid, None)
            return result
