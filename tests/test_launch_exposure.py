from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.review.service import ReviewService


def test_launch_records_no_exposure_for_unrelated_runs(store, config, episode):
    review = ReviewService(store)
    assert not review.exposure(episode)["records"]
    app = create_app(store.root, config)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        response = client.post("/api/runs", headers=headers, json={})
        assert response.status_code == 200, response.text
        app.state.runs.thread.join(30)
    assert not app.state.runs.thread.is_alive()
    assert response.json()["episode_id"] != episode
    assert not review.exposure(episode)["records"]


def test_idle_stop_records_no_exposure_and_does_not_stop_the_next_run(store, config, episode):
    review = ReviewService(store)
    app = create_app(store.root, config)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        stopped = client.post("/api/stop", headers=headers)
        assert stopped.status_code == 200 and stopped.json() == {"stop_requested": True}
        assert not review.exposure(episode)["records"]
        started = client.post("/api/runs", headers=headers, json={})
        assert started.status_code == 200, started.text
        app.state.runs.thread.join(30)
    assert store.summary(started.json()["episode_id"])["outcome"] != "OPERATOR_ABORT"
    assert not review.exposure(episode)["records"]
