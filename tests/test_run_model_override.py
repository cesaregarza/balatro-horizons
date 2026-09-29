from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import ROOT, load_config


def client_for(store):
    config = load_config(ROOT / "configs/luna-smoke.yaml")
    app = create_app(store.root, config)
    client = TestClient(app)
    headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
    return app, client, headers


def test_run_model_settings_override_only_the_selected_run(store):
    app, client, headers = client_for(store)
    original = app.state.config.models["luna"].settings.copy()
    captured = {}

    def start(config, agent, seed, **kwargs):
        captured.update(config=config, agent=agent, kwargs=kwargs)
        return "a" * 32

    app.state.runs.start = start
    try:
        reply = client.post("/api/runs", headers=headers, json={
            # The initial config key is the alias ``luna``; the frontend selects
            # and submits this canonical provider/model key before any settings save.
            "agent": "model:openai:gpt-5.6-luna", "offline": True,
            "model_settings": {"reasoning_effort": "high"},
            "cost_override": 10,
        })
    finally:
        client.close()
    assert reply.status_code == 200
    assert captured["agent"] == "luna"
    assert captured["config"].models["luna"].settings == {
        **original, "reasoning_effort": "high",
    }
    assert captured["config"].budgets.max_episode_cost_usd == 10
    assert captured["config"].budgets.max_batch_cost_usd == 10
    assert captured["config"].budgets.paid_calls_enabled is False
    assert app.state.config.models["luna"].settings == original


def test_model_settings_reject_invalid_values_and_baseline_overrides(store):
    app, client, headers = client_for(store)
    app.state.runs.start = lambda *_args, **_kwargs: "b" * 32
    try:
        unsupported = client.post("/api/runs", headers=headers, json={
            "agent": "luna", "model_settings": {"not_a_setting": 1},
        })
        invalid = client.post("/api/runs", headers=headers, json={
            "agent": "luna", "model_settings": {"reasoning_effort": "minimal"},
        })
        baseline = client.post("/api/runs", headers=headers, json={
            "agent": "heuristic", "model_settings": {"reasoning_effort": "high"},
        })
        unknown_model = client.post("/api/runs", headers=headers, json={
            "agent": "model:openai:gpt-6-nonexistent", "offline": True,
            "model_settings": {"reasoning_effort": "high"},
        })
    finally:
        client.close()
    assert unsupported.status_code == 400 and unsupported.json()["error"] == "INVALID_MODEL_SETTINGS"
    assert invalid.status_code == 400 and invalid.json()["error"] == "INVALID_MODEL_SETTINGS"
    assert baseline.status_code == 400
    assert baseline.json()["error"] == "MODEL_SETTINGS_REQUIRE_CONFIGURED_MODEL"
    assert unknown_model.status_code == 400
    assert unknown_model.json()["error"] == "MODEL_NOT_CONFIGURED"
