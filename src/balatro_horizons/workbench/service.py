"""Opt-in staged review sessions and workbench provenance."""

import json
import uuid

from balatro_horizons.review.service import ReviewError, ReviewService
from balatro_horizons.storage.journal import atomic_json, locked


class WorkbenchService(ReviewService):
    """Progressive reveal operations layered over read-only journal projections."""

    def __init__(self, store):
        super().__init__(store)
        # Explore tokens must not be accepted by staged-review mutations when
        # both route families are mounted in the same process.
        self.session_root = store.root / "workbench"
        self.session_root.mkdir(exist_ok=True)

    def open(self, eid, *, retrospective=False, prior_seed_exposure=False):
        self.store.manifest(eid)
        token = uuid.uuid4().hex
        session = {
            "episode_id": eid,
            "decision_index": 0,
            "stage": "observation",
            "mode": "retrospective" if retrospective else "prospective",
        }
        if retrospective:
            self.expose(
                eid,
                "retrospective_open",
                outcome_seen=True,
                model_identity_seen=True,
                max_event_seen=len(self.store.events(eid)) - 1,
            )
        if prior_seed_exposure:
            self.expose(eid, "prior_seed_exposure", prior_seed_exposure=True)
        atomic_json(self.session_root / (token + ".json"), session, immutable=True)
        if retrospective and not any(
            event["type"] == "observation" for event in self.store.events(eid)
        ):
            return {"review_token": token, "view": None}
        return {"review_token": token, "view": self.view(token)}

    def view(self, token):
        _, session = self.session(token)
        return self._view(session)

    def _view(self, session):
        result = self._build_view(session, staged=True)
        _, observations, _, _ = self._decision(session)
        index, stage = _frontier(session)
        result["navigation"] = {
            "decisions": [row["observation_id"] for row in observations[:index + 1]],
            "frontier_decision": observations[index]["observation_id"],
            "frontier_stage": stage,
            "at_frontier": (session["decision_index"], session["stage"]) == (index, stage),
        }
        return result

    def revisit(self, token, decision):
        """Return to already revealed information without widening the frontier."""
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = json.loads(path.read_text())
            frontier, stage = _frontier(session)
            _, observations, _, _ = self._decision(session)
            index = next((i for i, row in enumerate(observations[:frontier + 1])
                          if row["observation_id"] == decision), None)
            if index is None:
                raise ReviewError("REVIEW_DECISION_NOT_REVEALED")
            session.update(frontier_index=frontier, frontier_stage=stage,
                           decision_index=index, stage=stage if index == frontier else "transition")
            atomic_json(path, session)
        return self._view(session)

    def advance(self, token):
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = json.loads(path.read_text())
            frontier, stage = _frontier(session)
            if session["decision_index"] < frontier:
                session["decision_index"] += 1
                session["stage"] = stage if session["decision_index"] == frontier else "transition"
            elif session["stage"] == "observation":
                session["stage"] = "action"
            elif session["stage"] == "action":
                session["stage"] = "transition"
            else:
                _, observations, _, _ = self._decision(session)
                if session["decision_index"] + 1 >= len(observations):
                    raise ReviewError("NO_NEXT_DECISION")
                session["decision_index"] += 1
                session["stage"] = "observation"
            if session["decision_index"] >= frontier:
                frontier, stage = session["decision_index"], session["stage"]
            session.update(frontier_index=frontier, frontier_stage=stage)
            atomic_json(path, session)
        return self._view(session)

    def seek(self, token, decision):
        """Move the workbench cursor to an actual recorded decision ID."""
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = self._at_decision(json.loads(path.read_text()), decision)
            frontier, stage = _frontier(session)
            if session["decision_index"] >= frontier:
                frontier, stage = session["decision_index"], session["stage"]
            session.update(frontier_index=frontier, frontier_stage=stage)
            atomic_json(path, session)
        return self._view(session)


def _frontier(session):
    """Old sessions start from their existing cursor, never from journal length."""
    return (session.get("frontier_index", session["decision_index"]),
            session.get("frontier_stage", session["stage"]))


__all__ = ["ReviewError", "WorkbenchService"]
