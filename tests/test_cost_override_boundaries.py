"""HTTP/config entrypoints fail closed; all execution is synthetic or mocked."""

from unittest.mock import Mock

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.cli import main
from balatro_horizons.config import Config, load_config
from balatro_horizons.evaluation.batches import plan_batch
from balatro_horizons.storage.journal import atomic_json, digest

harness = test_campaign_budget.harness
MODES = ["agent_continue", "single_action_override", "short_human_sequence", "human_takeover"]
CAPS = ["max_episode_cost_usd", "max_batch_cost_usd"]


def branch_app(h, cap, *, paid=True, agent="luna"):
    frozen = h.config.model_copy(deep=True)
    setattr(frozen.budgets, cap, "uncapped")
    parent = h.store.create({"agent": agent}, {"config": frozen.model_dump()})
    h.config.budgets.paid_calls_enabled = paid
    app = create_app(h.store.root, h.config, workbench_enabled=True)
    app.state.runs.branch = Mock(return_value="b" * 32)
    return app, parent


@pytest.mark.parametrize("cap", CAPS)
@pytest.mark.parametrize("mode", MODES)
def test_inherited_uncapped_requires_new_branch_confirmation(harness, cap, mode):
    h = harness
    app, parent = branch_app(h, cap)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        payload = {"episode_id": parent, "decision": 0, "mode": mode}
        reply = client.post("/api/branches", headers=headers, json=payload)
        assert reply.json()["error"] == "UNCAPPED_CONFIRMATION_REQUIRED"
        app.state.runs.branch.assert_not_called()
        for invalid in (1, "true", None):
            reply = client.post("/api/branches", headers=headers, json={**payload, "confirm_uncapped": invalid})
            assert reply.status_code == 422
        app.state.runs.branch.assert_not_called()
        assert client.post("/api/branches", headers=headers, json={**payload, "confirm_uncapped": True}).status_code == 200
        app.state.runs.branch.assert_called_once()
    assert not h.calls and not h.games and len(h.store.list_episodes()) == 1
    h.native.assert_not_called()


@pytest.mark.parametrize("mode", MODES[:3])
def test_frozen_paid_permission_does_not_override_global_branch_gate(harness, mode):
    app, parent = branch_app(harness, CAPS[0], paid=False)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/branches", headers=headers, json={
            "episode_id": parent, "decision": 0, "mode": mode, "confirm_uncapped": True,
        })
        assert reply.json()["error"] == "PAID_EXECUTION_NOT_AUTHORIZED"
    app.state.runs.branch.assert_not_called()
    assert not harness.calls and not harness.games and len(harness.store.list_episodes()) == 1


@pytest.mark.parametrize("agent,mode", [("luna", "human_takeover"), ("heuristic", "agent_continue")])
def test_nonpaid_branch_can_pass_global_gate_but_still_confirms_uncapped(harness, agent, mode):
    app, parent = branch_app(harness, CAPS[0], paid=False, agent=agent)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/branches", headers=headers, json={
            "episode_id": parent, "decision": 0, "mode": mode, "confirm_uncapped": True,
        })
        assert reply.status_code == 200
    app.state.runs.branch.assert_called_once()


@pytest.mark.parametrize("cap", CAPS)
def test_run_fallback_confirms_inherited_uncapped_without_override(harness, cap):
    h = harness
    setattr(h.config.budgets, cap, "uncapped")
    app = create_app(h.store.root, h.config)
    app.state.runs.start = Mock(return_value="b" * 32)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/runs", headers=headers, json={"agent": "luna", "offline": True})
        assert reply.json()["error"] == "UNCAPPED_CONFIRMATION_REQUIRED"
    app.state.runs.start.assert_not_called()
    assert not h.calls and not h.games and not h.store.list_episodes()


@pytest.mark.parametrize("cap", CAPS)
def test_uncapped_cannot_be_saved_or_loaded_as_operator_defaults(harness, cap):
    h = harness
    app = create_app(h.store.root, h.config)
    original = h.config.model_dump()
    budgets = {**original["budgets"], cap: "uncapped"}
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.put("/api/settings", headers=headers, json={"budgets": budgets, "models": original["models"]})
        assert reply.status_code == 422 and "UNCAPPED_REQUIRES_RUN_OVERRIDE" in reply.text
    assert app.state.config.model_dump() == original and not app.state.settings_path.exists()
    atomic_json(app.state.settings_path, {**original, "budgets": budgets})
    with pytest.raises(ValueError, match="UNCAPPED_REQUIRES_RUN_OVERRIDE"):
        create_app(h.store.root, h.config)


@pytest.mark.parametrize("cap", CAPS)
def test_yaml_and_cli_do_not_admit_uncapped_defaults(tmp_path, cap, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(f"budgets:\n  {cap}: uncapped\n")
    with pytest.raises(ValueError, match="UNCAPPED_REQUIRES_RUN_OVERRIDE"):
        load_config(path)
    for command in (["run", "--offline"], ["batch", "plan"]):
        assert main(["--data-dir", str(tmp_path / "data"), *command, "--config", str(path)]) == 1
        assert "UNCAPPED_REQUIRES_RUN_OVERRIDE" in capsys.readouterr().err


@pytest.mark.parametrize("cap", CAPS)
def test_batch_planning_and_existing_plan_execution_refuse_uncapped(harness, cap):
    h = harness
    plan = h.plan()
    setattr(h.config.budgets, cap, "uncapped")
    with pytest.raises(ValueError, match="UNCAPPED_REQUIRES_RUN_OVERRIDE"):
        h.plan()
    for operation in (h.service().preflight_batch, h.service().run_batch):
        with pytest.raises(ValueError, match="UNCAPPED_REQUIRES_RUN_OVERRIDE"):
            operation(h.config, plan["batch_id"], offline=True)
    assert not h.calls and not h.games and not h.store.list_episodes()


def test_integer_yaml_caps_preserve_float_serialization_and_frozen_batch_hash(harness, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("budgets:\n  max_episode_cost_usd: 1\n  max_batch_cost_usd: 2\n")
    config = load_config(path)
    expected = Config(budgets={"max_episode_cost_usd": 1.0, "max_batch_cost_usd": 2.0}).model_dump()
    dumped = config.model_dump()
    assert type(dumped["budgets"]["max_episode_cost_usd"]) is float
    assert type(dumped["budgets"]["max_batch_cost_usd"]) is float
    assert digest(dumped) == digest(expected)
    plan = plan_batch(harness.store, Config.model_validate(expected), {"seeds": ["FIXTURE"]}, ["heuristic"], 1)
    assert plan["config_hash"] == digest(expected)
    harness.service().preflight_batch(config, plan["batch_id"], offline=True)


@pytest.mark.parametrize("uncapped", [False, True])
def test_branch_capability_discloses_frozen_limit_not_current_defaults(harness, uncapped):
    h = harness
    if uncapped:
        h.config.budgets.max_episode_cost_usd = "uncapped"
    result = h.service().execute(h.config, "luna", "FIXTURE", offline=True)
    h.config.budgets.max_episode_cost_usd = 1.0
    calls, games = len(h.calls), len(h.games)
    with TestClient(create_app(h.store.root, h.config, workbench_enabled=True)) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        opened = client.post("/api/reviews", headers=headers, json={"episode_id": result["episode_id"]}).json()
        reply = client.get("/api/review/branch-capability", headers={"X-Review-Token": opened["review_token"]})
        assert reply.json() == {"enabled": True, "reason": None, "requires_uncapped_confirmation": uncapped}
    assert (len(h.calls), len(h.games)) == (calls, games)
