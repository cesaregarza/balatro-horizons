"""Opt-in staged review sessions and workbench provenance."""

import json
import os
import uuid

from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.review.service import ReviewError, ReviewService
from balatro_horizons.storage.journal import atomic_json, identifier, locked, now


class WorkbenchService(ReviewService):
    """Progressive reveal operations layered over read-only journal projections."""

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
        atomic_json(self.root / (token + ".json"), session, immutable=True)
        if retrospective and not any(
            event["type"] == "observation" for event in self.store.events(eid)
        ):
            return {"review_token": token, "view": None}
        return {"review_token": token, "view": self.view(token)}

    def view(self, token):
        _, session = self.session(token)
        return self._view(session)

    def _view(self, session):
        events, observations, start, segment = self._decision(session)
        stage = session["stage"]
        eid = session["episode_id"]
        result = {
            "episode_id": eid,
            "decision": start["observation_id"],
            "stage": stage,
            "observation": start["payload"],
            "evidence_kind": self.store.manifest(eid)["evidence_kind"],
            "evaluation_eligible": self.store.manifest(eid).get("evaluation_eligible", False),
            "fixture": self.store.manifest(eid).get("fixture"),
        }
        max_seen = start["sequence"]
        if stage in ("action", "transition"):
            result["action_events"] = self._action_events(segment)
            max_seen = max([max_seen] + [event["sequence"] for event in result["action_events"]])
        if stage == "transition":
            max_seen = self._transition(result, observations, session, eid, events, max_seen)
        result["trajectory"] = self._trajectory(observations, max_seen)
        self.expose(eid, "review_" + stage, max_event_seen=max_seen)
        exposure = self.exposure(eid)
        result["exposure"] = {key: value for key, value in exposure.items() if key != "records"}
        result["review_mode"] = (
            "retrospective"
            if session["mode"] == "retrospective"
            else "mixed"
            if exposure["outcome_seen"]
            or exposure["prior_seed_exposure"]
            or exposure["max_event_seen"] > max_seen
            else "prospective"
        )
        return result

    def _transition(self, result, observations, session, eid, events, max_seen):
        index = session["decision_index"]
        if index + 1 < len(observations):
            result["transition"] = observations[index + 1]["payload"]
            result["can_advance"] = True
            return observations[index + 1]["sequence"]
        result["terminal"] = self.store.summary(eid)
        result["can_advance"] = False
        if result["terminal"]:
            self.expose(eid, "terminal_revealed", outcome_seen=True, max_event_seen=len(events) - 1)
        return max_seen

    def advance(self, token):
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = json.loads(path.read_text())
            if session["stage"] == "observation":
                session["stage"] = "action"
            elif session["stage"] == "action":
                session["stage"] = "transition"
            else:
                _, observations, _, _ = self._decision(session)
                if session["decision_index"] + 1 >= len(observations):
                    raise ReviewError("NO_NEXT_DECISION")
                session["decision_index"] += 1
                session["stage"] = "observation"
            atomic_json(path, session)
        return self.view(token)

    def seek(self, token, decision):
        """Move the workbench cursor to an actual recorded decision ID."""
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = self._at_decision(json.loads(path.read_text()), decision)
            atomic_json(path, session)
        return self._view(session)

    def annotate(self, token, data: AnnotationInput):
        _, session = self.session(token)
        view = self.view(token)
        eid = session["episode_id"]
        if not 0 <= data.start_decision <= data.end_decision <= view["decision"]:
            raise ReviewError("ANNOTATION_OUTSIDE_REVEALED_RANGE")
        exposed = {
            event["event_id"]
            for event in self.store.events(eid)
            if event["sequence"] <= view["exposure"]["max_event_seen"]
        }
        if not set(data.evidence_event_ids) <= exposed:
            raise ReviewError("UNSEEN_EVIDENCE")
        aid = data.annotation_id or uuid.uuid4().hex
        identifier(aid)
        path = self.root / (eid + "-annotations.jsonl")
        with locked(path.with_suffix(".lock")):
            existing = self.annotations(eid)
            revisions = [row for row in existing if row["annotation_id"] == aid]
            if data.annotation_id and not revisions:
                raise ReviewError("UNKNOWN_ANNOTATION")
            record = {
                **data.model_dump(),
                "annotation_id": aid,
                "episode_id": eid,
                "reviewer_id": "local-owner",
                "revision": len(revisions) + 1,
                "created_at": now(),
                "review_mode": view["review_mode"],
                "exposure": view["exposure"],
                "stage": view["stage"],
            }
            with path.open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return record


__all__ = ["ReviewError", "WorkbenchService"]
