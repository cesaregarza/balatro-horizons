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


def _integer_like_aliases(app):
    models = app.state.config.models
    luna = models.pop("luna")
    models["10"] = luna.model_copy(update={
        "input_usd_per_million": 0.3, "settings": {**luna.settings, "reasoning_effort": "high"},
    })
    models["2"] = luna.model_copy(update={
        "input_usd_per_million": 0.1, "settings": {**luna.settings, "reasoning_effort": "low"},
    })
    return models


def test_exact_alias_launch_uses_that_aliases_pricing_and_overlays_run_settings(store):
    app, client, headers = client_for(store)
    models = _integer_like_aliases(app)
    captured = {}

    def start(config, agent, seed, **kwargs):
        captured.update(config=config, agent=agent)
        return "a" * 32

    app.state.runs.start = start
    try:
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "2", "offline": True, "model_settings": {"reasoning_effort": "medium"},
        })
    finally:
        client.close()
    assert reply.status_code == 200, reply.text
    assert captured["agent"] == "2"
    launched = captured["config"].models["2"]
    assert launched.input_usd_per_million == 0.1
    assert launched.settings == {**models["2"].settings, "reasoning_effort": "medium"}


def test_canonical_launch_matching_several_aliases_is_refused(store):
    app, client, headers = client_for(store)
    _integer_like_aliases(app)
    app.state.runs.start = lambda *_args, **_kwargs: "a" * 32
    try:
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "model:openai:gpt-5.6-luna", "offline": True,
        })
    finally:
        client.close()
    assert reply.status_code == 400
    assert reply.json()["error"] == "AMBIGUOUS_CONFIGURED_MODEL"


def _launch_shown_alias(store, rebind):
    # The browser launches "2" with the revision its catalog showed. The old catalog had
    # none, so its payload was the bare alias.
    app, client, headers = client_for(store)
    models = app.state.config.models
    luna = models.pop("luna")
    models["2"] = luna.model_copy(update={"input_usd_per_million": 1.0})
    launched = []
    app.state.runs.start = lambda config, agent, *_a, **_k: launched.append(
        (agent, config.models[agent].model, config.models[agent].input_usd_per_million),
    ) or "a" * 32
    try:
        shown = client.get("/api/bootstrap").json()["config"]
        revision = shown.get("model_revisions", {}).get("2")
        if rebind:
            rebound = {**shown["models"], "2": {
                **shown["models"]["2"], "model": "gpt-5.6-sol", "input_usd_per_million": 9.0,
            }}
            saved = client.put("/api/settings", headers=headers, json={
                "models": rebound, "budgets": shown["budgets"], "skills": shown["skills"],
            })
            assert saved.status_code == 200, saved.text
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "2", "offline": True, "model_settings": shown["models"]["2"]["settings"],
            **({"model_revision": revision} if revision else {}),
        })
    finally:
        client.close()
    return reply, launched


def test_launch_of_an_alias_rebound_after_it_was_shown_is_refused(store):
    reply, launched = _launch_shown_alias(store, rebind=True)
    assert launched == []
    assert reply.status_code == 400
    assert reply.json()["error"] == "MODEL_CONFIG_CHANGED"


def test_launch_of_an_unchanged_shown_alias_starts_that_record(store):
    reply, launched = _launch_shown_alias(store, rebind=False)
    assert reply.status_code == 200, reply.text
    assert launched == [("2", "gpt-5.6-luna", 1.0)]
