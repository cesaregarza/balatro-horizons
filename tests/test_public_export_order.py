"""Pure ordering guard for the public export snapshot boundary."""

from types import SimpleNamespace

from balatro_horizons.evaluation import privacy, reports
from balatro_horizons.review import service as review_service


def test_export_scans_before_exposing_snapshot(monkeypatch):
    calls = []

    class Store:
        def manifest(self, eid, private=False):
            calls.append("manifest-private" if private else "manifest")
            return {"episode_id": eid, "evidence_kind": "SYNTHETIC_TEST", "seed": "secret"}

        def events(self, eid):
            calls.append("events")
            return [
                {"type": "episode_start", "payload": {"agent_protocol": "v7"}},
                {"type": "run_note", "payload": {}, "event_id": "n", "sequence": 1,
                 "observation_id": None},
                {"type": "terminal", "payload": {}, "event_id": "t", "sequence": 2,
                 "observation_id": None},
            ]

        def summary(self, eid):
            calls.append("summary")
            return {"outcome": "GAME_LOSS"}

    reviewer = SimpleNamespace(
        annotations=lambda eid: calls.append("annotations") or [],
        expose=lambda *args, **kwargs: calls.append("expose"),
    )
    monkeypatch.setattr(review_service, "ReviewService", lambda store: reviewer)
    monkeypatch.setattr(privacy, "scan", lambda value, secrets: calls.append("scan"))

    result = reports.episode_export(Store(), "episode")

    assert result["agent_protocol"] == "v7"
    assert calls == [
        "manifest", "events", "summary", "annotations", "events", "summary",
        "manifest-private", "scan", "events", "expose",
    ]


def test_reports_reexports_public_provider_projection():
    assert reports.public_provider_payload({"encrypted_content": "private"}) == {
        "opaque_continuation_omitted": True
    }
