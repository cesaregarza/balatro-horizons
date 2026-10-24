import pytest
from fastapi.testclient import TestClient

from balatro_horizons import service as run_service
from balatro_horizons.api import create_app
from balatro_horizons.api.routes_settings import model_revision
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


def test_launch_checks_the_same_config_it_starts_when_a_restore_lands_mid_request(store):
    # Shown: "2" is Luna at 1 (revision A). Settings swap in Sol at 9; the launch copies
    # that config, then a restore puts A back before the revision is checked.
    app, client, headers = client_for(store)
    models = app.state.config.models
    luna = models.pop("luna")
    models["2"] = luna.model_copy(update={"input_usd_per_million": 1.0})
    shown = app.state.config
    rebound = shown.model_copy(deep=True)
    rebound.models["2"] = luna.model_copy(update={"model": "gpt-5.6-sol", "input_usd_per_million": 9.0})
    copy = rebound.model_copy

    def copy_then_restore(*args, **kwargs):
        captured = copy(*args, **kwargs)
        app.state.config = shown
        return captured

    object.__setattr__(rebound, "model_copy", copy_then_restore)
    started = []
    app.state.runs.start = lambda config, agent, *_a, **_k: started.append(
        (agent, config.models[agent].model, config.models[agent].input_usd_per_million),
    ) or "a" * 32
    try:
        revision = client.get("/api/bootstrap").json()["config"]["model_revisions"]["2"]
        app.state.config = rebound
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "2", "offline": True, "model_revision": revision,
        })
    finally:
        client.close()
    # Either the shown record launches, or nothing does; never check A and start B.
    assert started in ([], [("2", "gpt-5.6-luna", 1.0)]), started
    assert (reply.status_code, reply.json().get("error")) == (
        (400, "MODEL_CONFIG_CHANGED") if not started else (200, None)
    )

BUILT_IN = ("heuristic", "random_legal", "human")


def _policy_client(store, monkeypatch, models):
    # Launches go through the real route; the recorder asks the real RunService.policy which
    # player it would run. Paid admission is not under test here, and no provider client is built.
    app, client, headers = client_for(store)
    app.state.config.models.clear()
    app.state.config.models.update(models)
    runs = app.state.runs
    monkeypatch.setattr(runs, "validate_policy", lambda *_args: None)
    monkeypatch.setattr(run_service, "DirectProvider", lambda model, _limits: ("model", model.model, model.input_usd_per_million))
    played = []

    def start(config, agent, *_args, **_kwargs):
        policy = run_service.RunService.policy(runs, config, agent)
        played.append(policy if isinstance(policy, tuple) else type(policy).__name__)
        return "a" * 32

    runs.start = start
    return app, client, headers, played


@pytest.mark.parametrize("reserved", BUILT_IN)
def test_a_model_alias_named_like_a_built_in_player_never_runs_that_player(store, monkeypatch, reserved):
    # The reviewer's case: one Luna model under a reserved alias and under "luna2".
    luna = load_config(ROOT / "configs/luna-smoke.yaml").models["luna"]
    models = {reserved: luna.model_copy(update={"input_usd_per_million": 1.0}),
              "luna2": luna.model_copy(update={"input_usd_per_million": 2.0})}
    app, client, headers, played = _policy_client(store, monkeypatch, models)
    assert app.state.config.workbench_enabled is False
    try:
        replies = [client.post("/api/runs", headers=headers, json={
            "agent": key, "offline": True, "model_settings": dict(models[key].settings),
            "model_revision": model_revision(models[key]),
        }) for key in (reserved, "luna2")]
    finally:
        client.close()
    assert ("Baseline" not in played) and ("HumanPolicy" not in played), played
    assert (replies[0].status_code, replies[0].json().get("error")) == (400, "RESERVED_MODEL_ALIAS")
    assert (replies[1].status_code, played) == (200, [("model", "gpt-5.6-luna", 2.0)])


def test_a_canonical_launch_never_resolves_to_a_reserved_alias(store, monkeypatch):
    luna = load_config(ROOT / "configs/luna-smoke.yaml").models["luna"]
    _, client, headers, played = _policy_client(store, monkeypatch, {"human": luna})
    try:
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "model:openai:gpt-5.6-luna", "offline": True,
        })
    finally:
        client.close()
    assert played == []
    assert (reply.status_code, reply.json().get("error")) == (400, "RESERVED_MODEL_ALIAS")


def test_settings_refuse_a_model_alias_named_like_a_built_in_player(store):
    app, client, headers = client_for(store)
    try:
        shown = client.get("/api/bootstrap").json()["config"]
        reply = client.put("/api/settings", headers=headers, json={
            "models": {**shown["models"], "random_legal": shown["models"]["luna"]},
            "budgets": shown["budgets"], "skills": shown["skills"],
        })
        after = client.get("/api/bootstrap").json()["config"]
    finally:
        client.close()
    assert (reply.status_code, reply.json().get("error")) == (400, "RESERVED_MODEL_ALIAS")
    assert "random_legal" not in after["models"]


@pytest.mark.parametrize(("agent", "player"), [
    ("heuristic", "Baseline"), ("random_legal", "Baseline"), ("human", "HumanPolicy"),
])
def test_built_in_players_launch_as_before(store, monkeypatch, agent, player):
    luna = load_config(ROOT / "configs/luna-smoke.yaml").models["luna"]
    _, client, headers, played = _policy_client(store, monkeypatch, {"luna": luna})
    try:
        reply = client.post("/api/runs", headers=headers, json={"agent": agent, "offline": True})
    finally:
        client.close()
    assert (reply.status_code, played) == (200, [player])
