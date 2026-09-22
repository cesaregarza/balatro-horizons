import json
import time

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.replay import restore_seed_prefix
from balatro_horizons.observations.projection import HandleIssuer
from balatro_horizons.storage.journal import digest


def test_seed_restore_waits_before_first_comparison():
    class SlowStartup(FakeGame):
        ready = False

        def wait_ready(self):
            self.ready = True

        def observe_private(self):
            assert self.ready
            return super().observe_private()

    game = SlowStartup()
    expected = FakeGame().observe_private()
    restore_seed_prefix(
        game,
        {
            "initial_continuation_hash": digest(expected),
            "initial_issuer": HandleIssuer().snapshot(),
            "steps": [],
            "episode_id": "e" * 32,
        },
    )
    assert game.ready


def test_fixture_labels_and_export_download_are_separate_from_review(
    store, workbench_config, monkeypatch, tmp_path
):
    import balatro_horizons.api as api_module

    monkeypatch.setattr(api_module, "ROOT", tmp_path)
    config = workbench_config
    app = create_app(store.root, config)
    with TestClient(app) as client:
        token = client.get("/api/bootstrap").json()["operator_token"]
        op = {"X-BH-Operator": token}
        panel = client.post("/api/panels", headers=op, json={"count": 2}).json()
        plan = client.post(
            "/api/batches",
            headers=op,
            json={
                "panel_id": panel["panel_id"],
                "agents": ["heuristic", "random_legal"],
                "replicates": 1,
            },
        ).json()
        app.state.runs.run_batch(config, plan["batch_id"], offline=True)
        report = client.get("/api/batches/" + plan["batch_id"] + "/report", headers=op).json()
        assert report["evidence_kinds"] == ["SYNTHETIC_TEST"]
        exported = client.post("/api/batches/" + plan["batch_id"] + "/export", headers=op).json()
        assert exported["episodes"] == 4
        response = client.get("/api" + exported["download"], headers=op)
        assert (
            response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        )
        assert response.json()["export_policy"] == "public-schema-v1"
        assert client.get("/api" + exported["download"]).status_code == 403
        eid = store.create(
            {
                "evidence_kind": "NATIVE",
                "config": config.public(),
                "fixture": "test_setup",
                "evaluation_eligible": False,
            }
        )
        listed = client.get("/api/episodes", headers=op).json()
        assert next(e for e in listed if e["episode_id"] == eid)["fixture"] == "test_setup"
        from balatro_horizons.evaluation.reports import episode_export

        assert episode_export(store, eid)["manifest"]["fixture"] == "test_setup"
        assert "private_runs" not in json.dumps(response.json())


@pytest.mark.parametrize("mode", ["agent_continue", "short_human_sequence", "human_takeover"])
def test_intervention_modes_use_shared_human_api(store, workbench_config, episode, mode):
    """Drive synthetic human operations through the same endpoint as the browser/CLI."""
    from balatro_horizons.evidence.certification import verify_checkpoint
    from balatro_horizons.harness.baselines import Baseline

    config = workbench_config
    assert verify_checkpoint(store, config, episode, 0)["status"] == "passed"
    parent_head = store.summary(episode)["journal_head"]
    app = create_app(store.root, config)
    with TestClient(app) as client:
        op = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        response = client.post(
            "/api/branches",
            headers=op,
            json={"episode_id": episode, "decision": 0, "mode": mode},
        )
        assert response.status_code == 200
        child = response.json()["episode_id"]
        sent = set()
        deadline = time.monotonic() + 10
        try:
            while not store.summary(child) and time.monotonic() < deadline:
                current = client.get("/api/operator/human", headers=op).json()
                if current["waiting"]:
                    decision = current["context"]["observation"]["observation_id"]
                    if decision not in sent:
                        operation = Baseline("heuristic").decide(current["context"], [])
                        assert (
                            client.post(
                                "/api/operator/human", headers=op, json=operation
                            ).status_code
                            == 200
                        )
                        sent.add(decision)
                time.sleep(0.01)
            assert store.summary(child)["outcome"] == "WIN"
            commits = [e for e in store.events(child) if e["type"] == "action_commit"]
            humans = [e for e in commits if e["actor"] == "human"]
            if mode == "short_human_sequence":
                assert len(humans) == 3 and len(commits) > 3
                assert all(e["actor"] == "agent" for e in commits[3:])
            elif mode == "human_takeover":
                assert len(humans) == len(commits) > 0
            else:
                assert not humans
            assert not store.manifest(child)["evaluation_eligible"]
            assert store.summary(episode)["journal_head"] == parent_head
        finally:
            app.state.runs.stop.set()
            app.state.runs.thread.join(3)


def test_direct_human_runs_cannot_be_scored_as_autonomous(store, workbench_config, monkeypatch):
    from balatro_horizons.review.service import ReviewService
    from balatro_horizons.service import RunService

    service = RunService(store, ReviewService(store))
    monkeypatch.setattr(service, "_launch", lambda task: None)
    config = workbench_config
    eid = service.start(config, "human", "TEST_PANEL_SEED", offline=False)
    assert store.manifest(eid)["evaluation_eligible"] is False
    assert store.manifest(eid)["assistance"] == "human_takeover"
    from balatro_horizons.evaluation.batches import plan_batch

    with pytest.raises(ValueError, match="MANUAL_RUNS_NOT_BATCH_AGENTS"):
        plan_batch(store, config, {"seeds": ["TEST_PANEL_SEED"]}, ["human"])
