"""Episode-only review ledgers with verified public ancestry; fake games only."""

import ast
import inspect
import json

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.cli import main as cli_main
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.evidence.continuation_probe import verify_continuation_probe
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.review import action_accounting, decision_ledger, summary_projection
from balatro_horizons.review.decision_ledger import build_summary, summarize, summary_input
from balatro_horizons.review.export import export_response
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.workbench.branches import prepare_branch

harness = test_campaign_budget.harness


def branch(store, config, parent, decision):
    assert verify_checkpoint(store, config, parent, decision)["status"] == "passed"
    service = RunService(store, ReviewService(store))
    child = service.branch(config, parent, decision, "agent_continue")
    service.thread.join(10)
    assert not service.thread.is_alive() and service.error is None
    return child


def assert_surfaces(store, eid, own, inherited, tmp_path, capsys):
    report = build_summary(store, eid)
    accounting = {
        "ledger_scope": "episode", "own_committed_actions": own,
        "inherited_committed_actions": inherited,
        "total_committed_actions": own + inherited, "terminal_count_scope": "lineage",
    }
    assert report["action_accounting"] == accounting
    assert report["ledger_action_count"] == len(report["actions"]) == own
    assert report["summary"]["committed_actions"] == own + inherited
    for format in ("json", "jsonl"):
        response = export_response(report, format)
        text = response.body.decode()
        rows = [json.loads(text)] if format == "json" else [json.loads(s) for s in text.splitlines()]
        assert rows and all(row["action_accounting"] == accounting for row in rows)
        assert "DO_NOT_EXPORT_THIS_SEED" not in text and "PRIVATE_FIXTURE" not in text
    output, markdown = tmp_path / f"{eid}.json", tmp_path / f"{eid}.md"
    assert cli_main([
        "--data-dir", str(store.root), "summarize", "--episode-id", eid,
        "--output", str(output), "--markdown-output", str(markdown),
    ]) == 0
    assert json.loads(output.read_text())["action_accounting"] == accounting
    assert json.loads(capsys.readouterr().out)["recorded_actions"] == own
    assert f"{own} own; {inherited} inherited" in markdown.read_text()


def test_root_and_nonzero_branch_exports_and_cli(store, config, episode, tmp_path, capsys):
    parent_bytes = (store.episode_path(episode) / "events.jsonl").read_bytes()
    child = branch(store, config, episode, 2)
    assert_surfaces(store, child, 3, 2, tmp_path, capsys)
    assert_surfaces(store, episode, 5, 0, tmp_path, capsys)
    grandchild = branch(store, config, child, 3)
    assert_surfaces(store, grandchild, 2, 3, tmp_path, capsys)
    assert (store.episode_path(episode) / "events.jsonl").read_bytes() == parent_bytes


def test_nonzero_budget_child_exports_and_cli(harness, tmp_path, capsys):
    h = harness
    h.config.budgets.max_episode_cost_usd = 0.019
    h.config.budgets.max_batch_cost_usd = 1
    root = h.service().execute(h.config, "luna", "PRIVATE_FIXTURE", offline=True)["episode_id"]
    terminal = h.store.summary(root)
    assert terminal["outcome"] == "BUDGET_EXHAUSTED"
    inherited = terminal["committed_actions"]
    assert inherited > 0
    observation = next(e["payload"] for e in reversed(h.store.events(root)) if e["type"] == "observation")
    decision = observation["observation_id"]
    action = Baseline("heuristic").decide({"observation": observation}, [])["envelope"]["action"]
    assert verify_continuation_probe(h.store, h.config, root, decision, action)["status"] == "passed"
    service = h.service()
    child = service.continue_budget(root, 2, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive() and service.error is None
    own = sum(e["type"] == "action_commit" for e in h.store.events(child))
    assert own > 0
    assert_surfaces(h.store, child, own, inherited, tmp_path, capsys)


@pytest.mark.parametrize("surface,session_path", [("explore", "explore/sessions"), ("review", "reviews")])
@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_child_download_routes(store, workbench_config, episode, surface, session_path, format):
    child = branch(store, workbench_config, episode, 2)
    app = create_app(store.root, workbench_config)
    with TestClient(app) as client:
        operator = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        session = client.post(f"/api/{session_path}", headers=operator, json={
            "episode_id": child, "retrospective": True,
        }).json()
        response = client.get(f"/api/{surface}/export/{format}", headers={
            "X-Review-Token": session["review_token"],
        })
        assert response.status_code == 200
        data = response.json() if format == "json" else json.loads(response.text.splitlines()[0])
        assert data["action_accounting"]["own_committed_actions"] == 3
        assert data["action_accounting"]["inherited_committed_actions"] == 2
        assert data["run_summary"]["committed_actions"] == 5
        assert client.get(f"/api/{surface}/export/{format}").status_code == 403


@pytest.mark.parametrize("ambiguous", [False, True])
def test_recovered_child_keeps_own_terminal_scope(store, config, episode, ambiguous):
    completed = branch(store, config, episode, 2)
    child, _, _ = prepare_branch(store, config, episode, 2, "agent_continue")
    for event in store.events(completed):
        store.append(child, event["type"], event["payload"], **{
            key: event[key] for key in ("observation_id", "request_id", "actor")
        })
        if event["type"] == "observation" and event["observation_id"] == 3:
            break
    if ambiguous:
        store.append(child, "action_intent", {
            "observation_id": 3, "action": {"type": "leave_shop"},
        }, observation_id=3, request_id="unresolved")
    assert child in store.recover()
    report = build_summary(store, child)
    assert report["ledger_action_count"] == report["summary"]["committed_actions"] == 1
    assert report["action_accounting"] == {
        "ledger_scope": "episode", "own_committed_actions": 1,
        "inherited_committed_actions": 2, "total_committed_actions": 3,
        "terminal_count_scope": "episode",
    }


def test_pre_start_failure_and_live_child_counts(store, config, episode):
    assert verify_checkpoint(store, config, episode, 2)["status"] == "passed"
    child, _, _ = prepare_branch(store, config, episode, 2, "agent_continue")
    live = build_summary(store, child)
    assert live["ledger_action_count"] == 0 and live["summary"] is None
    assert live["action_accounting"]["inherited_committed_actions"] == 2
    store.finish(child, {"committed_actions": 0, "outcome": "INFRASTRUCTURE_FAILURE"})
    report = build_summary(store, child)
    assert report["action_accounting"]["terminal_count_scope"] == "episode"
    assert report["action_accounting"]["own_committed_actions"] == 0


def test_real_count_mismatch_still_fails_closed(store, config, episode):
    child = branch(store, config, episode, 2)
    for eid in (episode, child):
        public = summary_input(store, eid)
        public["summary"]["committed_actions"] += 1
        with pytest.raises(ValueError, match="^ACTION_TOTAL_MISMATCH$"):
            summarize(public)


def test_bad_public_ancestry_fails_without_review_exposure(store, config, episode, monkeypatch):
    child = branch(store, config, episode, 2)
    before = ReviewService(store).exposure(child)
    original = store.manifest
    def manifest(eid, private=False):
        result = original(eid, private)
        if eid == child and not private:
            result["parent_prefix_hash"] = "0" * 64
        return result
    monkeypatch.setattr(store, "manifest", manifest)
    with pytest.raises(ValueError, match="^BRANCH_PREFIX_MISMATCH$"):
        build_summary(store, child)
    assert ReviewService(store).exposure(child) == before


def test_accounting_change_keeps_small_functions_and_shrinks_ledger_module():
    touched = {"summary_input", "summarize", "_committed_rows", "build_summary"}
    for module in (action_accounting, summary_projection, decision_ledger):
        source = inspect.getsource(module)
        if module is decision_ledger:
            assert len(source.splitlines()) <= 444
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and (module is not decision_ledger or node.name in touched):
                assert node.end_lineno - node.lineno + 1 <= 60, node.name
