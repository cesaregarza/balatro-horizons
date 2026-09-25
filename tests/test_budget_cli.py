"""Package entry points use synthetic records and mocked operator requests only."""

import json
from unittest.mock import Mock

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from test_budget_continuation import stopped

from balatro_horizons.api import create_app
from balatro_horizons.cli import continue_budget, main
from balatro_horizons.config import Config
from balatro_horizons.review import run_status

harness = test_campaign_budget.harness


def test_package_budget_plan_does_not_contact_worker(harness, monkeypatch, capsys):
    eid, _, _, _ = stopped(harness)
    request = Mock(side_effect=AssertionError("plan must not contact a worker"))
    monkeypatch.setattr(continue_budget, "operator_request", request)
    assert main(["continue-budget", eid, "--data-dir", str(harness.store.root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["launches"] == {"restoration_verification": 0, "paid_continuation": 1}
    assert result["status"]["continuation"]["restoration"] == "ready_for_single_restore"
    assert result["status"]["continuation"]["checkpoint_saved"]
    request.assert_not_called()


@pytest.mark.parametrize("status,exit_code", [("passed", 0), ("failed", 1)])
def test_package_budget_verify_keeps_probe_separate(harness, monkeypatch, capsys, status, exit_code):
    eid, _, decision, action = stopped(harness)
    response = {"status": status, "certificate_id": "c" * 32,
                "completed_repetitions": 3 if status == "passed" else 0, "failures": []}
    request = Mock(return_value=response)
    monkeypatch.setattr(continue_budget, "operator_request", request)
    assert main(["--data-dir", str(harness.store.root), "continue-budget", eid, "--verify"]) == exit_code
    request.assert_called_once_with("/verify", "POST", {
        "mode": "checkpoint_probe", "episode_id": eid, "decision": decision, "probe_action": action,
    }, timeout=600)
    assert json.loads(capsys.readouterr().out) == response


@pytest.mark.parametrize("flags", [
    ["--start"], ["--combined-cap-usd", "1"],
    ["--start", "--combined-cap-usd", "nan"],
    ["--start", "--combined-cap-usd", "-1"],
])
def test_package_budget_requires_explicit_finite_funding(monkeypatch, capsys, flags):
    request = Mock(side_effect=AssertionError("invalid funding must not reach worker"))
    monkeypatch.setattr(continue_budget, "operator_request", request)
    assert main(["continue-budget", "e" * 32, *flags]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    request.assert_not_called()


def test_package_budget_start_posts_cap_and_parent_head(harness, monkeypatch, capsys):
    eid, terminal, _, _ = stopped(harness)
    request = Mock(return_value={"episode_id": "c" * 32})
    monkeypatch.setattr(continue_budget, "operator_request", request)
    assert main(["continue-budget", eid, "--data-dir", str(harness.store.root),
                 "--start", "--combined-cap-usd", "1"]) == 0
    request.assert_called_once_with(f"/operator/episodes/{eid}/continue-budget", "POST", {
        "combined_cap_usd": 1.0, "parent_terminal_hash": terminal["journal_head"],
    })
    assert json.loads(capsys.readouterr().out) == {"episode_id": "c" * 32}


def test_budget_route_requires_workbench_even_with_operator_token(store):
    with TestClient(create_app(store.root, Config())) as client:
        token = client.get("/api/bootstrap").json()["operator_token"]
        response = client.post("/api/operator/episodes/unknown/continue-budget", json={
            "combined_cap_usd": 1, "parent_terminal_hash": "a" * 64,
        }, headers={"X-BH-Operator": token})
        assert response.status_code == 404


def test_documented_workbench_route_counts(tmp_path):
    routes = []
    for enabled in (False, True):
        app = create_app(tmp_path / str(enabled), Config(workbench_enabled=enabled))
        routes.append({
            (path, method) for path, methods in app.openapi()["paths"].items()
            for method in methods if method in {"get", "post", "put", "patch", "delete"}
        })
    off, on = routes
    assert len(off) == 24 and len(on) == 44
    assert off < on and len(on - off) == 20
    assert ("/api/operator/episodes/{eid}/continue-budget", "post") in on - off
    assert {("/api/operator/episodes/{eid}/restore", method) for method in ("get", "post")} <= on - off


def test_cost_stopped_status_checks_recovery_readiness_without_a_probe(harness, monkeypatch):
    eid, _, _, _ = stopped(harness)
    recovery = Mock(side_effect=ValueError("RECOVERY_SENTINEL"))
    monkeypatch.setattr(run_status, "recovery_checkpoint", recovery)
    result = run_status.run_status(harness.store, eid)
    assert result["continuation"]["restoration"] == "RECOVERY_SENTINEL"
    recovery.assert_called_once()
