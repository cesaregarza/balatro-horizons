"""Controls for repairing a legacy model alias that uses a built-in player name.

These pass before the browser repair exists: the settings route already refuses a reserved
alias, and these pin that repaired maps save normally and that run records stay unchanged.
"""

import json

from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.config import ROOT, load_config


def legacy_client(store):
    # Loaded configuration from before reserved names were refused.
    config = load_config(ROOT / "configs/luna-smoke.yaml")
    luna = config.models.pop("luna")
    config.models["human"] = luna.model_copy(update={"input_usd_per_million": 1.0})
    config.models["luna2"] = luna.model_copy(update={"input_usd_per_million": 2.0})
    app = create_app(store.root, config)
    client = TestClient(app)
    headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
    return app, client, headers


def save(client, headers, shown, models):
    return client.put("/api/settings", headers=headers, json={
        "models": models, "budgets": shown["budgets"], "skills": shown["skills"],
    })


def test_control_the_stored_legacy_map_is_refused_and_nothing_persists(store):
    app, client, headers = legacy_client(store)
    try:
        shown = client.get("/api/bootstrap").json()["config"]
        reply = save(client, headers, shown, shown["models"])
    finally:
        client.close()
    assert (reply.status_code, reply.json()["error"]) == (400, "RESERVED_MODEL_ALIAS")
    assert not app.state.settings_path.exists()
    assert set(app.state.config.models) == {"human", "luna2"}


def test_control_a_repaired_map_saves_and_an_ordinary_save_follows(store):
    app, client, headers = legacy_client(store)
    run = store.create({"agent": "human", "evidence_kind": "SYNTHETIC_TEST",
                        "config": {"models": {"human": {"model": "gpt-5.6-luna"}}}})
    try:
        shown = client.get("/api/bootstrap").json()["config"]
        models = {key: value for key, value in shown["models"].items() if key != "human"}
        renamed = save(client, headers, shown, {**models, "luna-legacy": shown["models"]["human"]})
        persisted = client.get("/api/bootstrap").json()["config"]
        ordinary = save(client, headers, persisted, persisted["models"])
    finally:
        client.close()
    assert renamed.status_code == 200 and ordinary.status_code == 200
    saved = json.loads(app.state.settings_path.read_text())["models"]
    assert set(saved) == {"luna-legacy", "luna2"}
    assert saved["luna-legacy"]["input_usd_per_million"] == 1.0
    # Repair changes only the current configuration; a run's recorded config is untouched.
    assert store.manifest(run)["config"]["models"] == {"human": {"model": "gpt-5.6-luna"}}
