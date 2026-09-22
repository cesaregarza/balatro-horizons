import json

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.service import RunService
from balatro_horizons.workbench.branches import prepare_branch
from balatro_horizons.workbench.service import ReviewError, WorkbenchService


def test_prospective_open_does_not_claim_model_or_outcome_exposure(store, episode):
    view = WorkbenchService(store).open(episode)["view"]
    assert view["review_mode"] == "prospective"
    assert view["exposure"]["model_identity_seen"] is False
    assert view["exposure"]["outcome_seen"] is False


def test_AT16_cursor_does_not_reveal_future(store, episode, workbench_config):
    config = workbench_config
    app = create_app(store.root, config)
    with TestClient(app) as client:
        op = client.get("/api/bootstrap").json()["operator_token"]
        retrospective = client.post(
            "/api/reviews",
            json={"episode_id": episode, "retrospective": True},
            headers={"X-BH-Operator": op},
        ).json()
        retrospective_headers = {"X-Review-Token": retrospective["review_token"]}
        assert client.post(
            "/api/review/seek",
            json={"decision": 4},
            headers=retrospective_headers,
        ).status_code == 200
        assert client.post(
            "/api/review/annotations",
            json=annotation(end_decision=4).model_dump(),
            headers=retrospective_headers,
        ).status_code == 200
        opened = client.post(
            "/api/reviews", json={"episode_id": episode}, headers={"X-BH-Operator": op}
        )
        assert opened.status_code == 200
        token = opened.json()["review_token"]
        headers = {"X-Review-Token": token}
        assert client.get("/api/review/annotations", headers=headers).json() == []
        first = client.get("/api/review", headers=headers).json()
        assert "terminal" not in first and "action_events" not in first and "total" not in first
        assert "WIN" not in json.dumps(first)
        assert client.get("/api/operator/status", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/reviews",
                json={"episode_id": episode, "decision_index": 5},
                headers={"X-BH-Operator": op},
            ).status_code
            == 422
        )
        action = client.post("/api/review/advance", headers=headers).json()
        assert action["stage"] == "action" and "transition" not in action
        after = client.post("/api/review/advance", headers=headers).json()
        assert after["transition"]["observation_id"] == 1


def annotation(**kwargs):
    return AnnotationInput.model_validate(
        {
            "start_decision": 0,
            "end_decision": 0,
            "judgment": "concern",
            "mechanism_summary": "Save money for future shops",
            "confidence": "medium",
            "horizons_in_tension": ["immediate", "long_term"],
            **kwargs,
        }
    )


def test_AT17_revisions_preserve_original_provenance(store, episode):
    r = WorkbenchService(store)
    opened = r.open(episode)
    token = opened["review_token"]
    first = r.annotate(token, annotation())
    assert first["review_mode"] == "prospective"
    r.expose(
        episode, "operator_status", outcome_seen=True, model_identity_seen=True, max_event_seen=999
    )
    second = r.annotate(
        token,
        annotation(annotation_id=first["annotation_id"], mechanism_summary="Revised after outcome"),
    )
    assert second["revision"] == 2 and second["review_mode"] == "mixed"
    assert r.annotations(episode)[0] == first
    with pytest.raises(ReviewError):
        r.annotate(token, annotation(end_decision=3))


def test_AT10_AT18_checkpoint_and_immutable_override(store, episode, workbench_config):
    config = workbench_config
    before = (store.episode_path(episode) / "events.jsonl").read_bytes()
    cert = verify_checkpoint(store, config, episode, 0, repetitions=3)
    assert cert["status"] == "passed" and cert["evidence_kind"] == "SYNTHETIC_TEST"
    service = RunService(store, WorkbenchService(store))
    initial = next(e["payload"] for e in store.events(episode) if e["type"] == "observation")
    action = {"type": "skip_blind", "blind_id": initial["state"]["revealed_blinds"][0]["id"]}
    bid = service.branch(config, episode, 0, "single_action_override", [action])
    service.thread.join(10)
    assert store.summary(bid)["outcome"] == "WIN"
    assert store.manifest(bid)["evaluation_eligible"] is False
    assert store.manifest(bid)["parent_episode_id"] == episode
    assert (store.episode_path(episode) / "events.jsonl").read_bytes() == before
    first = next(e for e in store.events(bid) if e["type"] == "agent_context")
    assert "memory" not in first["payload"]["context"]["observation"]
    assert "original future" not in json.dumps(first)


def test_AT24_uncertified_branch_is_rejected(store, episode, workbench_config):
    config = workbench_config
    with pytest.raises(ValueError, match="CHECKPOINT_NOT_CERTIFIED"):
        prepare_branch(store, config, episode, 0, "agent_continue")


def test_operator_cross_origin_is_rejected(store, workbench_config):
    with TestClient(create_app(store.root, workbench_config)) as client:
        assert (
            client.get(
                "/api/bootstrap", headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/runs", json={}).status_code == 403
