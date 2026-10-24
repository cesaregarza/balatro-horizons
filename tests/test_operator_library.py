from contextlib import contextmanager

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
            "deck", "stake", "branch",
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


def test_blinded_library_carries_no_model_identity(store):
    # The blinded list must not reveal which model ran, even in fields the UI does not render.
    store.create({
        "evidence_kind": "NATIVE", "agent": "model:openai:gpt-6-luna",
        "config": {"deck": "RED", "stake": "GOLD",
                   "models": {"model:openai:gpt-6-luna": {"provider": "openai", "model": "gpt-6-luna"}}},
    })
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        body = client.get("/api/episodes", headers=headers).text
    assert "gpt-6-luna" not in body and '"agent"' not in body


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


def _finished_run(store, agent):
    eid = store.create({
        "evidence_kind": "NATIVE", "agent": agent,
        "config": {"deck": "RED", "stake": "GOLD", "models": {agent: {"model": agent}}},
    })
    store.finish(eid, {"outcome": "WIN", "reason": "TEST_STOP"})
    return eid


def _ledger(store, eid):
    path = store.root / "review" / f"{eid}-exposure.jsonl"
    return path.read_text().splitlines() if path.exists() else []


@contextmanager
def _operator_client(store):
    app = create_app(store.root, Config())
    with TestClient(app) as client:
        client.headers["X-BH-Operator"] = client.get("/api/bootstrap").json()["operator_token"]
        yield app, client


def test_operator_details_expose_only_the_runs_chosen_even_when_a_run_lands_mid_request(store):
    chosen = _finished_run(store, "model-a")
    with _operator_client(store) as (app, client):
        listed = app.state.store.list_episodes
        late = []

        def insert_before_enumeration():
            if not late:
                late.append(_finished_run(app.state.store, "model-b"))
            return listed()

        app.state.store.list_episodes = insert_before_enumeration
        response = client.post("/api/operator/episodes", json={"episode_ids": [chosen]})
        blinded = client.get("/api/episodes").json()
    assert response.status_code == 200
    assert [row["episode_id"] for row in response.json()] == [chosen]
    assert {row["episode_id"] for row in blinded} == {chosen, late[0]}
    assert len(_ledger(store, chosen)) == 1
    assert _ledger(store, late[0]) == []


def test_operator_details_for_an_empty_selection_expose_and_record_nothing(store):
    run = _finished_run(store, "model-a")
    with _operator_client(store) as (_, client):
        response = client.post("/api/operator/episodes", json={"episode_ids": []})
    assert response.status_code == 200 and response.json() == []
    assert _ledger(store, run) == []


def test_operator_details_ignore_requested_ids_outside_the_library(store):
    run = _finished_run(store, "model-a")
    foreign = "f" * 32
    with _operator_client(store) as (_, client):
        response = client.post("/api/operator/episodes", json={"episode_ids": [foreign, run]})
    assert response.status_code == 200
    assert [row["episode_id"] for row in response.json()] == [run]
    assert _ledger(store, foreign) == []
