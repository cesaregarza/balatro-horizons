import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.review.service import ReviewService


def live_episode(store):
    eid = store.create({"agent": "test", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(
        eid,
        "observation",
        {"phase": "SHOP", "state": {"progress": {"ante": 5}, "hand": ["PRIVATE_TO_STATUS"]}},
    )
    store.append(eid, "provider_request", {"reserved_usd": 0.2}, request_id="one")
    return eid


def test_unchanged_status_reuses_verification_and_exposure(store):
    eid = live_episode(store)
    archived = store.create({"agent": "old"})
    store.finish(archived, {"outcome": "WIN"})
    review = ReviewService(store)
    status = OperatorStatus(store, review)
    with patch.object(store, "events", wraps=store.events) as reads:
        first = status.episodes()
        assert reads.call_count == 2
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(row == first for row in pool.map(lambda _: status.episodes(), range(8)))
        assert reads.call_count == 2
    assert len(review.exposure(eid)["records"]) == 1
    assert len(review.exposure(archived)["records"]) == 1
    assert "PRIVATE_TO_STATUS" not in json.dumps(first)
    row = next(r for r in first if r["episode_id"] == eid)
    assert row["progress"]["cost_usd"] == 0.2


def test_changed_episode_refreshes_costs_and_authoritative_terminal_only(store):
    eid = live_episode(store)
    archived = store.create({"agent": "old"})
    store.finish(archived, {"outcome": "WIN"})
    review = ReviewService(store)
    status = OperatorStatus(store, review)
    status.episodes()
    store.append(eid, "provider_response", {"cost_usd": 0.03}, request_id="one")
    store.append(eid, "action_commit", {})
    with patch.object(store, "events", wraps=store.events) as reads:
        row = next(r for r in status.episodes() if r["episode_id"] == eid)
        assert reads.call_args_list == [((eid,), {})]
    assert row["progress"]["cost_usd"] == 0.03
    assert row["progress"]["committed_actions"] == 1
    # Terminal can precede SQLite indexing; polling still reflects the verified journal.
    store.append(eid, "terminal", {"outcome": "GAME_LOSS", "cost_usd": 0.03})
    row = next(r for r in status.episodes() if r["episode_id"] == eid)
    assert row["summary"]["outcome"] == "GAME_LOSS" and row["progress"] is None
    exposure = review.exposure(eid)
    assert exposure["outcome_seen"] and exposure["max_event_seen"] == 4
    assert len(exposure["records"]) == 3


def test_cache_does_not_conceal_journal_rewrite(store):
    eid = live_episode(store)
    status = OperatorStatus(store, ReviewService(store))
    status.episodes()
    path = store.episode_path(eid) / "events.jsonl"
    stamp = path.stat()
    original = path.read_text()
    changed = original.replace('"ante":5', '"ante":9')
    assert changed != original and len(changed) == len(original)
    path.write_text(changed)
    # Make the metadata change deterministic even on a coarsely clocked test filesystem.
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
    with pytest.raises(ValueError, match="JOURNAL_INTEGRITY_FAILURE"):
        status.episodes()


def test_status_endpoint_preserves_operator_boundary_and_cached_response(store, config):
    eid = live_episode(store)
    with TestClient(create_app(store.root, config)) as client:
        assert client.get("/api/operator/status").status_code == 403
        token = client.get("/api/bootstrap").json()["operator_token"]
        first = client.get("/api/operator/status", headers={"X-BH-Operator": token})
        second = client.get("/api/operator/status", headers={"X-BH-Operator": token})
        assert first.status_code == 200 and second.json() == first.json()
        assert first.json()["episodes"][0]["episode_id"] == eid
        assert not first.json()["running"]


def _status_ledger(store, eid):
    path = store.root / "review" / f"{eid}-exposure.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    return [record for record in map(json.loads, lines) if record["kind"] == "operator_status"]


@contextmanager
def _status_client(store, config):
    with TestClient(create_app(store.root, config)) as client:
        client.headers["X-BH-Operator"] = client.get("/api/bootstrap").json()["operator_token"]
        yield client


def test_watching_exposes_only_the_frozen_runs_even_when_one_launches_mid_watch(store, config):
    watched = live_episode(store)
    with _status_client(store, config) as client:
        status = client.app.state.operator_status
        listed = status.store.list_episodes
        launched = []

        def launch_before_enumeration():
            if not launched:
                launched.append(live_episode(status.store))
            return listed()

        status.store.list_episodes = launch_before_enumeration
        first = client.post("/api/operator/status", json={"episode_ids": [watched]})
        second = client.post("/api/operator/status", json={"episode_ids": [watched]})
    assert first.status_code == 200 and second.status_code == 200
    assert [row["episode_id"] for row in first.json()["episodes"]] == [watched]
    assert [row["episode_id"] for row in second.json()["episodes"]] == [watched]
    assert len(_status_ledger(store, watched)) == 1
    assert _status_ledger(store, launched[0]) == []


def test_watching_an_empty_set_records_nothing(store, config):
    run = live_episode(store)
    with _status_client(store, config) as client:
        reply = client.post("/api/operator/status", json={"episode_ids": []})
    assert reply.status_code == 200
    assert reply.json() == {"running": False, "active_episode": None, "error": None, "episodes": []}
    assert _status_ledger(store, run) == []


def test_worker_state_needs_no_run_ids_and_records_no_exposure(store, config):
    run = live_episode(store)
    with _status_client(store, config) as client:
        reply = client.get("/api/operator/status")
    assert reply.status_code == 200
    assert reply.json() == {"running": False, "active_episode": None, "error": None}
    assert _status_ledger(store, run) == []
