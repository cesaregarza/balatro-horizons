import json

from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.review.detail_projection import compact_detail
from balatro_horizons.review.service import ReviewService


def test_compact_detail_retains_boards_and_returned_summaries_without_technical_bodies():
    view = {"observation": {"state": "before"}, "transition": {"state": "after"},
            "action_events": [
                {"type": "provider_request", "payload": {"body": "large-request-sentinel"}},
                {"type": "helper_result", "payload": {"text": "large-helper-sentinel"}},
                {"type": "provider_response", "event_id": "response-id", "payload": {"body": {
                    "output": [{"type": "reasoning", "encrypted_content": "opaque-sentinel",
                                "summary": [{"type": "summary_text", "text": "Save interest."}]},
                               {"type": "function_call", "arguments": "large-arguments-sentinel"}],
                }}},
            ]}
    compact = compact_detail(view)
    assert compact["observation"] == view["observation"]
    assert compact["transition"] == view["transition"]
    assert "Save interest." in json.dumps(compact)
    assert "sentinel" not in json.dumps(compact)
    assert len(view["action_events"]) == 3


def test_technical_records_remain_available_through_the_original_api(store, episode, monkeypatch):
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        operator = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        token = client.post("/api/explore/sessions", headers=operator, json={
            "episode_id": episode, "retrospective": True, "include_view": False,
        }).json()["review_token"]
        headers = {"X-Review-Token": token}
        compact = client.get("/api/explore/decisions/0?technical=false", headers=headers).json()
        full = client.get("/api/explore/decisions/0", headers=headers).json()
        assert compact["technical_records_included"] is False
        assert compact["observation"] == full["observation"]
        assert compact["transition"] == full["transition"]
        assert compact["action_events"] == []
        assert full["action_events"]


def test_ordinary_snapshot_matches_full_detail_without_decoding_technical_records(store, episode, monkeypatch):
    review = ReviewService(store)
    token = review.open_explorer(episode, include_view=False)["review_token"]
    decisions = [event["observation_id"] for event in store.events(episode)
                 if event["type"] == "observation"]
    full = {decision: compact_detail(review.decision(token, decision)) for decision in decisions}
    original = review._read_cache.events
    compact_reads = []

    def ordinary_only(source, eid, *, compact=False):
        compact_reads.append(compact)
        assert compact, "ordinary navigation decoded the full technical journal"
        return original(source, eid, compact=compact)

    monkeypatch.setattr(review._read_cache, "events", ordinary_only)
    for decision in decisions:
        assert review.decision(token, decision, technical=False) == full[decision]
    assert compact_reads
