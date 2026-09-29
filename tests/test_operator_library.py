from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.review.operator_library import project_episode


def test_operator_library_is_authenticated_and_blinded_library_stays_unchanged(store, episode):
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        assert client.get("/api/operator/episodes").status_code == 403
        token = client.get("/api/bootstrap").json()["operator_token"]
        headers = {"X-BH-Operator": token}
        blinded = client.get("/api/episodes", headers=headers).json()[0]
        assert set(blinded) == {
            "episode_id", "created_at", "evidence_kind", "evaluation_eligible", "fixture",
            "deck", "stake", "agent", "branch",
        }
        response = client.get("/api/operator/episodes", headers=headers)
        assert response.status_code == 200
        row = next(item for item in response.json() if item["episode_id"] == episode)
        assert row["model_name"] == "heuristic"
        assert row["parent_episode_id"] is None
        assert row["outcome"] is not None
        assert "seed" not in row and "config" not in row

        exposure_path = store.root / "review" / f"{episode}-exposure.jsonl"
        initial_records = exposure_path.read_text().splitlines()
        client.get("/api/operator/episodes", headers=headers)
        assert exposure_path.read_text().splitlines() == initial_records


def test_operator_library_projects_frozen_model_lineage_and_unknowns(store, episode):
    child = store.create({
        "evidence_kind": "NATIVE",
        "agent": "luna",
        "parent_episode_id": episode,
        "config": {
            "deck": "RED", "stake": "GOLD",
            "models": {"luna": {"model": "gpt-6-luna", "settings": {"reasoning_effort": "high"}}},
        },
    })
    store.finish(child, {
        "outcome": "INFRASTRUCTURE_FAILURE", "reason": "TEST_STOP",
        "cost_usd": 0.25, "committed_actions": 99,
    })
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        rows = {row["episode_id"]: row for row in client.get("/api/operator/episodes", headers=headers).json()}
    model = rows[child]
    assert model["parent_episode_id"] == episode
    assert model["model_name"] == "gpt-6-luna"
    assert model["reasoning_effort"] == "high"
    assert model["recorded_interface"] is None
    assert model["outcome"] == "INFRASTRUCTURE_FAILURE" and model["reason"] == "TEST_STOP"
    assert model["cost_usd"] == 0.25 and model["committed_actions"] is None


def test_single_operator_projection_is_index_only_and_hides_child_lineage_count(store, episode):
    child = store.create({
        "evidence_kind": "NATIVE", "agent": "random_legal",
        "parent_episode_id": episode, "config": {},
    })
    store.finish(child, {"outcome": "STOPPED", "cost_usd": 0.5, "committed_actions": 40})
    row = next(item for item in store.list_episodes() if item["episode_id"] == child)
    projection = project_episode(row)
    assert projection["model_name"] == "random_legal"
    assert projection["committed_actions"] is None
    assert projection["cost_usd"] == 0.5
