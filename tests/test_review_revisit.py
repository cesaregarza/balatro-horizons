"""Backward review navigation cannot reveal a future decision or stage."""

import json

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.review.service import ReviewError, ReviewService
from balatro_horizons.workbench.service import WorkbenchService


def test_revisit_preserves_the_exact_revealed_frontier(store, episode):
    service = WorkbenchService(store)
    opened = service.open(episode)
    token = opened["review_token"]
    first = opened["view"]["decision"]
    assert opened["view"]["navigation"]["decisions"] == [first]
    before = service.exposure(episode)
    path, session = service.session(token)
    with pytest.raises(ReviewError, match="REVIEW_DECISION_NOT_REVEALED"):
        service.revisit(token, first + 1)
    assert service.exposure(episode) == before
    assert json.loads(path.read_text()) == session
    service.advance(token)  # action
    service.advance(token)  # transition
    latest = service.advance(token)  # next observation only
    second = latest["decision"]
    assert latest["stage"] == "observation"
    prior = service.revisit(token, first)
    assert prior["stage"] == "transition"
    assert prior["navigation"] == {
        "decisions": [first, second], "frontier_decision": second,
        "frontier_stage": "observation", "at_frontier": False,
    }
    back = service.advance(token)
    assert back["decision"] == second and back["stage"] == "observation"
    assert "action_events" not in back and "terminal" not in back
    assert back["navigation"]["at_frontier"]
    assert service.advance(token)["stage"] == "action"
    service.revisit(token, first)
    assert service.revisit(token, second)["stage"] == "action"
    with pytest.raises(ReviewError, match="REVIEW_DECISION_NOT_REVEALED"):
        service.revisit(token, second + 1)


def test_legacy_cursor_is_the_only_initial_frontier(store, episode):
    service = WorkbenchService(store)
    token = service.open(episode)["review_token"]
    path, session = service.session(token)
    session.update(decision_index=1, stage="action")
    path.write_text(json.dumps(session))
    assert service.revisit(token, 0)["navigation"]["frontier_stage"] == "action"
    view = service.revisit(token, 1)
    assert view["stage"] == "action" and "transition" not in view


def test_revisit_uses_recorded_ids_not_position(store, episode, monkeypatch):
    service = WorkbenchService(store)
    events = store.events(episode)
    observations = [event for event in events if event["type"] == "observation"]
    for index, event in enumerate(observations):
        event["observation_id"] = 10 + index * 3
    monkeypatch.setattr(service, "_events", lambda eid: events)
    token = service.open(episode)["review_token"]
    for _ in range(3):
        service.advance(token)
    assert service.revisit(token, 10)["decision"] == 10
    assert service.revisit(token, 13)["stage"] == "observation"
    with pytest.raises(ReviewError, match="REVIEW_DECISION_NOT_REVEALED"):
        service.revisit(token, 1)


def test_revisit_route_keeps_the_flag_and_token_boundaries(store, episode, workbench_config):
    explorer_token = ReviewService(store).open_explorer(episode)["review_token"]
    with TestClient(create_app(store.root, workbench_config)) as client:
        operator = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        token = client.post("/api/reviews", headers=operator,
                            json={"episode_id": episode}).json()["review_token"]
        headers = {"X-Review-Token": token}
        assert client.post("/api/review/revisit", headers=headers,
                           json={"decision": 0}).status_code == 200
        assert client.post("/api/review/revisit", headers=headers,
                           json={"decision": 1}).status_code == 400
        assert client.post("/api/review/revisit", headers={"X-Review-Token": explorer_token},
                           json={"decision": 0}).status_code == 400
    workbench_config.workbench_enabled = False
    with TestClient(create_app(store.root, workbench_config)) as client:
        assert client.post("/api/review/revisit", headers=headers,
                           json={"decision": 0}).status_code == 404
