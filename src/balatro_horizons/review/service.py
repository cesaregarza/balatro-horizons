"""Server-owned progressive reveal cursors and append-only reviewer provenance."""

import json
import os
import uuid

from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.storage.journal import atomic_json, identifier, locked, now


class ReviewError(ValueError):
    pass


class ReviewService:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "review"
        self.root.mkdir(exist_ok=True)

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
            "max_event_seen": max((r.get("max_event_seen", -1) for r in records), default=-1),
            **{
                k: any(r.get(k, False) for r in records)
                for k in ("outcome_seen", "model_identity_seen", "prior_seed_exposure")
            },
            "records": records,
        }

    def expose(self, eid, kind, **fields):
        self._append(
            self.root / (identifier(eid) + "-exposure.jsonl"),
            {"kind": kind, "timestamp": now(), "reviewer_id": "local-owner", **fields},
        )

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

    def session(self, token):
        path = self.root / (identifier(token) + ".json")
        if not path.is_file():
            raise ReviewError("UNKNOWN_REVIEW_SESSION")
        return path, json.loads(path.read_text())

    def _decision(self, session):
        with locked(self.store.episode_path(session["episode_id"]) / ".writer.lock"):
            events = self.store.events(session["episode_id"])
        observations = [e for e in events if e["type"] == "observation"]
        i = session["decision_index"]
        if i >= len(observations):
            raise ReviewError("DECISION_NOT_AVAILABLE")
        start = observations[i]
        end = observations[i + 1]["sequence"] if i + 1 < len(observations) else len(events)
        segment = events[start["sequence"] + 1 : end]
        return events, observations, start, segment

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
            result["action_events"] = [
                e
                for e in segment
                if e["type"]
                in (
                    "agent_context",
                    "agent_operation",
                    "provider_request",
                    "provider_response",
                    "provider_error",
                    "provider_input_check",
                    "harness_failure",
                    "action_intent",
                    "action_commit",
                    "action_rejected",
                    "helper_result",
                    "run_note",
                )
            ]
            max_seen = max([max_seen] + [e["sequence"] for e in result["action_events"]])
        if stage == "transition":
            i = session["decision_index"]
            if i + 1 < len(observations):
                result["transition"] = observations[i + 1]["payload"]
                max_seen = observations[i + 1]["sequence"]
                result["can_advance"] = True
            else:
                result["terminal"] = self.store.summary(eid)
                result["can_advance"] = False
                if result["terminal"]:
                    self.expose(
                        eid, "terminal_revealed", outcome_seen=True, max_event_seen=len(events) - 1
                    )
        result["trajectory"] = [
            {
                "decision": e["observation_id"],
                "phase": e["payload"]["phase"],
                "resources": e["payload"]["state"]["resources"],
                "progress": e["payload"]["state"]["progress"],
                "build": [c["label"] for c in e["payload"]["state"]["jokers"]],
            }
            for e in observations
            if e["sequence"] <= max_seen
        ]
        self.expose(eid, "review_" + stage, max_event_seen=max_seen)
        exposure = self.exposure(eid)
        result["exposure"] = {k: v for k, v in exposure.items() if k != "records"}
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

    def advance(self, token):
        path, session = self.session(token)
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

    def decisions(self, token):
        """Full-run navigation is available only after explicit retrospective opening."""
        from balatro_horizons.review.decision_ledger import build_summary

        _, session = self.session(token)
        if session["mode"] != "retrospective":
            raise ReviewError("RETROSPECTIVE_REVIEW_REQUIRED")
        report = build_summary(self.store, session["episode_id"])
        return {
            key: report.get(key)
            for key in (
                "manifest",
                "summary",
                "actions",
                "uncommitted_actions",
                "pending_decisions",
                "rounds",
                "action_counts",
                "money_range",
                "source_journal_head",
            )
        }

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

    def decision(self, token, decision):
        """Read a detail without moving the annotation cursor or racing other reads."""
        _, session = self.session(token)
        return self._view(self._at_decision(session, decision))

    def seek(self, token, decision):
        """Move the annotation cursor to an actual decision ID, including branch IDs."""
        path, _ = self.session(token)
        with locked(path.with_suffix(".lock")):
            session = self._at_decision(json.loads(path.read_text()), decision)
            atomic_json(path, session)
        return self._view(session)

    def annotations(self, eid):
        path = self.root / (identifier(eid) + "-annotations.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def annotate(self, token, data: AnnotationInput):
        _, session = self.session(token)
        view = self.view(token)
        eid = session["episode_id"]
        if not 0 <= data.start_decision <= data.end_decision <= view["decision"]:
            raise ReviewError("ANNOTATION_OUTSIDE_REVEALED_RANGE")
        exposed = {
            e["event_id"]
            for e in self.store.events(eid)
            if e["sequence"] <= view["exposure"]["max_event_seen"]
        }
        if not set(data.evidence_event_ids) <= exposed:
            raise ReviewError("UNSEEN_EVIDENCE")
        aid = data.annotation_id or uuid.uuid4().hex
        identifier(aid)
        path = self.root / (eid + "-annotations.jsonl")
        with locked(path.with_suffix(".lock")):
            existing = self.annotations(eid)
            revisions = [r for r in existing if r["annotation_id"] == aid]
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
