"""Read-only dashboard projections and append-only retrospective annotations.

Progressive reveal and cursor advancement live in :mod:`workbench.service`.
This service can open an already-recorded run for retrospective inspection,
but it never advances a prospective cursor or exposes a staged view.
"""

import json
import os
import uuid

from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.storage.journal import atomic_json, identifier, locked, now

ACTION_EVENT_TYPES = frozenset({
    "agent_context", "agent_operation", "provider_request", "provider_response",
    "provider_error", "provider_input_check", "harness_failure", "action_intent",
    "action_commit", "action_rejected", "helper_result", "run_note",
})


class ReviewError(ValueError):
    pass


class ReviewService:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "review"
        self.root.mkdir(exist_ok=True)
        self.session_root = self.root

    def _append(self, path, row):
        with locked(path.with_suffix(".lock")):
            with path.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def exposure(self, eid):
        path = self.root / (identifier(eid) + "-exposure.jsonl")
        records = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        )
        return {
            "max_event_seen": max((row.get("max_event_seen", -1) for row in records), default=-1),
            **{
                key: any(row.get(key, False) for row in records)
                for key in ("outcome_seen", "model_identity_seen", "prior_seed_exposure")
            },
            "records": records,
        }

    def expose(self, eid, kind, **fields):
        self._append(
            self.root / (identifier(eid) + "-exposure.jsonl"),
            {"kind": kind, "timestamp": now(), "reviewer_id": "local-owner", **fields},
        )

    def session(self, token):
        path = self.session_root / (identifier(token) + ".json")
        if not path.is_file():
            raise ReviewError("UNKNOWN_REVIEW_SESSION")
        return path, json.loads(path.read_text())

    def open_explorer(self, eid, *, prior_seed_exposure=False):
        """Open a read-only retrospective session for ordinary dashboard use."""
        self.store.manifest(eid)
        token = uuid.uuid4().hex
        session = {
            "episode_id": eid,
            "decision_index": 0,
            "stage": "transition",
            "mode": "retrospective",
        }
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
        if not any(event["type"] == "observation" for event in self.store.events(eid)):
            return {"review_token": token, "view": None}
        return {"review_token": token, "view": self.explore_view(token)}

    def _decision(self, session):
        with locked(self.store.episode_path(session["episode_id"]) / ".writer.lock"):
            events = self.store.events(session["episode_id"])
        observations = [event for event in events if event["type"] == "observation"]
        index = session["decision_index"]
        if index >= len(observations):
            raise ReviewError("DECISION_NOT_AVAILABLE")
        start = observations[index]
        end = observations[index + 1]["sequence"] if index + 1 < len(observations) else len(events)
        return events, observations, start, events[start["sequence"] + 1 : end]

    def _at_decision(self, session, decision):
        if session["mode"] != "retrospective":
            raise ReviewError("RETROSPECTIVE_REVIEW_REQUIRED")
        with locked(self.store.episode_path(session["episode_id"]) / ".writer.lock"):
            events = self.store.events(session["episode_id"])
        observations = [event for event in events if event["type"] == "observation"]
        index = next(
            (i for i, event in enumerate(observations) if event["observation_id"] == decision), None
        )
        if index is None:
            raise ReviewError("DECISION_NOT_AVAILABLE")
        return {**session, "decision_index": index, "stage": "transition"}

    def _action_events(self, segment):
        return [event for event in segment if event["type"] in ACTION_EVENT_TYPES]

    def _trajectory(self, observations, max_seen):
        return [
            {
                "decision": event["observation_id"],
                "phase": event["payload"]["phase"],
                "resources": event["payload"]["state"]["resources"],
                "progress": event["payload"]["state"]["progress"],
                "build": [card["label"] for card in event["payload"]["state"]["jokers"]],
            }
            for event in observations
            if event["sequence"] <= max_seen
        ]

    def _build_view(self, session, *, staged=False):
        events, observations, start, segment = self._decision(session)
        stage = session["stage"] if staged else "transition"
        eid = session["episode_id"]
        manifest = self.store.manifest(eid)
        result = {
            "episode_id": eid,
            "decision": start["observation_id"],
            "stage": stage,
            "observation": start["payload"],
            "evidence_kind": manifest["evidence_kind"],
            "evaluation_eligible": manifest.get("evaluation_eligible", False),
            "fixture": manifest.get("fixture"),
        }
        max_seen = start["sequence"]
        if stage in ("action", "transition"):
            result["action_events"] = self._action_events(segment)
            if staged:
                max_seen = max(
                    [max_seen] + [event["sequence"] for event in result["action_events"]]
                )
        if stage == "transition":
            index = session["decision_index"]
            if index + 1 < len(observations):
                result["transition"] = observations[index + 1]["payload"]
                max_seen = observations[index + 1]["sequence"]
                result["can_advance"] = staged
            else:
                result["terminal"] = self.store.summary(eid)
                result["can_advance"] = False
                if staged and result["terminal"]:
                    self.expose(
                        eid,
                        "terminal_revealed",
                        outcome_seen=True,
                        max_event_seen=len(events) - 1,
                    )
        result["trajectory"] = self._trajectory(observations, max_seen)
        if staged:
            self.expose(eid, "review_" + stage, max_event_seen=max_seen)
        else:
            self.expose(
                eid,
                "retrospective_detail",
                max_event_seen=max_seen,
                outcome_seen=bool(result.get("terminal")),
                model_identity_seen=True,
            )
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

    def _view(self, session):
        return self._build_view(session)

    def explore_view(self, token):
        _, session = self.session(token)
        if session["mode"] != "retrospective":
            raise ReviewError("RETROSPECTIVE_REVIEW_REQUIRED")
        return self._view(session)

    def view(self, token):
        """Return the current read-only view for annotation and route callers."""
        return self.explore_view(token)

    def decisions(self, token):
        """Return the public ledger without widening the session capability."""
        from balatro_horizons.review.decision_ledger import build_summary

        _, session = self.session(token)
        if session["mode"] != "retrospective":
            raise ReviewError("RETROSPECTIVE_REVIEW_REQUIRED")
        report = build_summary(self.store, session["episode_id"])
        return {
            key: report.get(key)
            for key in (
                "manifest", "summary", "actions", "uncommitted_actions", "pending_decisions",
                "rounds", "action_counts", "action_accounting", "money_range", "source_journal_head",
            )
        }

    def decision(self, token, decision):
        _, session = self.session(token)
        return self._view(self._at_decision(session, decision))

    def seek(self, token, decision):
        """Resolve a detail without mutating the read-only dashboard session."""
        return self.decision(token, decision)

    def annotations(self, eid):
        path = self.root / (identifier(eid) + "-annotations.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def annotations_for_view(self, view):
        """Return only annotations whose end is already visible in a view."""
        return [
            annotation
            for annotation in self.annotations(view["episode_id"])
            if annotation["end_decision"] <= view["decision"]
        ]

    def _annotation_record(self, data, aid, eid, revisions, view):
        return {
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

    def annotate(self, token, data: AnnotationInput):
        _, session = self.session(token)
        if data.start_decision > data.end_decision:
            raise ReviewError("ANNOTATION_OUTSIDE_REVEALED_RANGE")
        if session["mode"] == "retrospective":
            view = self._view(self._at_decision(session, data.end_decision))
        else:
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
            record = self._annotation_record(data, aid, eid, revisions, view)
            with path.open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return record


__all__ = ["ReviewError", "ReviewService"]
