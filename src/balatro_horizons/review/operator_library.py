"""Compact operator run metadata; never scans journals for library rows."""

import math
from threading import Lock

from balatro_horizons.review.protocol_label import protocol_metadata


class OperatorLibrary:
    def __init__(self, store, review):
        self.store, self.review = store, review
        self._exposed = {}
        self._lock = Lock()

    def episodes(self):
        result = []
        with self._lock:
            for row in self.store.list_episodes():
                projection = project_episode(row)
                projection.update(protocol_metadata(self.store, row["episode_id"]))
                self._record_exposure(projection)
                result.append(projection)
            present = {item["episode_id"] for item in result}
            self._exposed = {eid: key for eid, key in self._exposed.items() if eid in present}
        return result

    def _record_exposure(self, row):
        eid = row["episode_id"]
        signature = (row["model_name"], row["outcome"], row["reason"])
        if self._exposed.get(eid) == signature:
            return
        self.review.expose(
            eid,
            "operator_library",
            model_identity_seen=True,
            outcome_seen=row["outcome"] is not None,
        )
        self._exposed[eid] = signature


def _baseline(agent):
    return agent if agent in {
        "heuristic", "random", "random_legal", "human", "evaluator_fixture",
    } else "unknown"


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _integer(value):
    return value if type(value) is int else None


def project_episode(row):
    """Project one indexed episode row without scanning its journal."""
    manifest, summary = row["manifest"], row["summary"] or {}
    agent = manifest.get("agent", "unknown")
    config = manifest.get("config") or {}
    model = (config.get("models") or {}).get(agent) or {}
    settings = model.get("settings") or {}
    identity = model.get("model")
    if not isinstance(identity, str) or not identity:
        identity = _baseline(agent)
    parent = manifest.get("parent_episode_id")
    parent = parent if isinstance(parent, str) else None
    effort, interface = settings.get("reasoning_effort"), manifest.get("recorded_interface")
    # The interface reference is stored in the episode_start journal event, not
    # the compact index. Do not scan each journal just to fill this optional label.
    outcome, reason = summary.get("outcome"), summary.get("reason")
    return {
        "episode_id": row["episode_id"],
        "created_at": manifest.get("created_at"),
        "evidence_kind": manifest.get("evidence_kind", "unknown"),
        "evaluation_eligible": manifest.get("evaluation_eligible", False),
        "fixture": manifest.get("fixture"),
        "deck": config.get("deck"),
        "stake": config.get("stake"),
        "agent": agent,
        "branch": bool(parent),
        "parent_episode_id": parent,
        "model_name": identity,
        "reasoning_effort": effort if isinstance(effort, str) else None,
        "recorded_interface": interface if isinstance(interface, str) else None,
        "context_policy": None,
        "provider_wire_policy": None,
        "outcome": outcome if isinstance(outcome, str) else None,
        "reason": reason if isinstance(reason, str) else None,
        "cost_usd": _number(summary.get("cost_usd")),
        # Terminal action counts for children include the parent's prefix.
        "committed_actions": None if parent else _integer(summary.get("committed_actions")),
    }
