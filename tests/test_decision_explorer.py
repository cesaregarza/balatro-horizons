"""Retrospective navigation must not widen prospective capabilities."""

import json

import pytest
from fastapi.testclient import TestClient
from test_review_branches import annotation

from balatro_horizons.api import create_app
from balatro_horizons.workbench.service import ReviewError
from balatro_horizons.workbench.service import WorkbenchService as ReviewService


def test_prospective_tokens_cannot_enumerate_or_seek(store, episode, workbench_config):
    config = workbench_config
    with TestClient(create_app(store.root, config)) as client:
        op = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post(
            "/api/reviews", json={"episode_id": episode}, headers={"X-BH-Operator": op}
        ).json()
        headers = {"X-Review-Token": opened["review_token"]}
        assert client.get("/api/review/decisions").status_code == 403
        assert client.get("/api/review/decisions", headers=headers).json() == {
            "error": "RETROSPECTIVE_REVIEW_REQUIRED"
        }
        for decision in (0, 1, 999):
            assert client.get(f"/api/review/decisions/{decision}", headers=headers).json() == {
                "error": "RETROSPECTIVE_REVIEW_REQUIRED"
            }
            response = client.post("/api/review/seek", json={"decision": decision}, headers=headers)
            assert response.status_code == 400
            assert response.json() == {"error": "RETROSPECTIVE_REVIEW_REQUIRED"}
        unchanged = client.get("/api/review", headers=headers).json()
        assert unchanged["decision"] == 0 and unchanged["stage"] == "observation"
        assert "terminal" not in unchanged and "action_events" not in unchanged


def test_explorer_jumps_both_ways_and_records_annotation_provenance(store, episode, workbench_config):
    config = workbench_config
    before = (store.episode_path(episode) / "events.jsonl").read_bytes()
    with TestClient(create_app(store.root, config)) as client:
        op = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post(
            "/api/reviews",
            json={"episode_id": episode, "retrospective": True},
            headers={"X-BH-Operator": op},
        ).json()
        headers = {"X-Review-Token": opened["review_token"]}
        report = client.get("/api/review/decisions", headers=headers).json()
        assert len(report["actions"]) == store.summary(episode)["committed_actions"]
        assert "DO_NOT_EXPORT_THIS_SEED" not in json.dumps(report)
        assert "provider_response" not in json.dumps(report)
        decision = report["actions"][-1]["decision"]
        detail = client.get(f"/api/review/decisions/{decision}", headers=headers).json()
        assert detail["decision"] == decision and detail["stage"] == "transition"
        assert client.get("/api/review", headers=headers).json()["decision"] == 0
        last = client.post("/api/review/seek", json={"decision": decision}, headers=headers).json()
        assert last["decision"] == decision and last["stage"] == "transition"
        assert last["review_mode"] == "retrospective"
        first = client.post("/api/review/seek", json={"decision": 0}, headers=headers).json()
        assert first["decision"] == 0
        saved = client.post(
            "/api/review/annotations", json=annotation().model_dump(), headers=headers
        ).json()
        assert saved["review_mode"] == "retrospective"
        assert saved["exposure"]["outcome_seen"]
        assert (
            client.post("/api/review/seek", json={"decision": "1"}, headers=headers).status_code
            == 422
        )
    assert (store.episode_path(episode) / "events.jsonl").read_bytes() == before


def test_seek_uses_actual_ids_for_branches_and_rejects_missing_ids(store):
    from test_boundary import project

    from balatro_horizons.game.fake import FakeGame

    eid = store.create({"evidence_kind": "SYNTHETIC_TEST"}, eid="e" * 32)
    observation = project(FakeGame().observe_private(), index=17).model_dump(mode="json")
    store.append(eid, "observation", observation, observation_id=17)
    review = ReviewService(store)
    token = review.open(eid, retrospective=True)["review_token"]
    assert review.seek(token, 17)["decision"] == 17
    with pytest.raises(ReviewError, match="DECISION_NOT_AVAILABLE"):
        review.seek(token, 0)
    assert review.view(token)["decision"] == 17


def test_explorer_can_explain_a_run_that_failed_before_any_observation(store):
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST", "agent": "fixture"})
    store.finish(
        eid,
        {
            "outcome": "INFRASTRUCTURE_FAILURE",
            "reason": "STARTUP_FAILED",
            "committed_actions": 0,
            "cost_usd": 0,
        },
    )
    review = ReviewService(store)
    opened = review.open(eid, retrospective=True)
    assert opened["view"] is None
    ledger = review.decisions(opened["review_token"])
    assert ledger["actions"] == []
    assert ledger["summary"]["reason"] == "STARTUP_FAILED"
    assert review.exposure(eid)["outcome_seen"]


def test_live_explorer_handles_intent_commit_settlement_and_terminal(store):
    from test_boundary import project

    from balatro_horizons.game.fake import FakeGame

    game = FakeGame()
    game.phase = "SHOP"
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST", "agent": "fixture"})
    before = project(game.observe_private()).model_dump(mode="json")
    store.append(eid, "observation", before, observation_id=0)
    review = ReviewService(store)
    token = review.open(eid, retrospective=True)["review_token"]
    assert review.decisions(token)["actions"] == []
    action = {
        "observation_id": 0,
        "action": {"type": "buy", "offer_id": before["state"]["offers"][0]["id"]},
    }
    store.append(eid, "action_intent", action, observation_id=0, request_id="buy")
    pending = review.decisions(token)
    assert pending["uncommitted_actions"][0]["status"] == "in_progress"
    store.append(eid, "action_commit", action, observation_id=0, request_id="buy")
    settling = review.decisions(token)
    assert settling["actions"] == []
    assert settling["uncommitted_actions"][0]["status"] == "awaiting_transition"
    assert settling["source_journal_head"] != pending["source_journal_head"]
    game.bought = True
    after = project(game.observe_private(), index=1).model_dump(mode="json")
    store.append(eid, "observation", after, observation_id=1)
    settled = review.decisions(token)
    assert len(settled["actions"]) == 1
    assert settled["uncommitted_actions"] == []
    assert settled["actions"][0]["decision"] == 0
    assert review.decision(token, 0)["transition"] == after
    store.finish(eid, {"outcome": "GAME_LOSS", "committed_actions": 1})
    finished = review.decisions(token)
    assert finished["summary"]["outcome"] == "GAME_LOSS"
    assert review.exposure(eid)["outcome_seen"]
