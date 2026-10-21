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
