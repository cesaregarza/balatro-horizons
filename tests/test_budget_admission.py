"""Continuation admission locks and preflights; no native or paid execution."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from unittest.mock import Mock

import pytest
import test_campaign_budget
from test_budget_continuation import certify, stopped

from balatro_horizons import service as service_module

harness = test_campaign_budget.harness


def test_busy_worker_refuses_budget_admission_without_waiting(harness):
    service = harness.service()
    with service.admission():
        with pytest.raises(ValueError, match="WORKER_BUSY"):
            service.continue_budget("f" * 32, 1, expected_head="a" * 64)
    assert harness.store.list_episodes() == []


def test_native_session_preflight_precedes_child_creation(harness, monkeypatch):
    eid, terminal, _, _ = stopped(harness)
    service = harness.service()
    original_manifest = harness.store.manifest

    def native_manifest(episode_id, *args, **kwargs):
        return {**original_manifest(episode_id, *args, **kwargs), "evidence_kind": "NATIVE"}

    monkeypatch.setattr(harness.store, "manifest", native_manifest)
    preflight = Mock(side_effect=ValueError("WINDOWS_SESSION_EXPIRED"))
    monkeypatch.setattr(service_module, "load_session", preflight)
    launch = Mock()
    monkeypatch.setattr(service, "_launch", launch)
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    preflight.assert_called_once()
    launch.assert_not_called()
    assert len(harness.store.list_episodes()) == 1
    harness.native.assert_not_called()


def test_budget_admission_obeys_a_foreign_process_lock(harness):
    eid, terminal, decision, action = stopped(harness)
    certify(harness, eid, decision, action)
    path = harness.store.episode_path(eid, True) / "budget-admission.lock"
    service = harness.service()
    service._launch = Mock()  # Admission only; no worker or provider starts.
    holder_code = (
        "import fcntl, sys\n"
        "with open(sys.argv[1], 'a') as stream:\n"
        "    fcntl.flock(stream, fcntl.LOCK_EX)\n"
        "    print('locked', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", holder_code, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        assert holder.stdout.readline().strip() == "locked"
        future = pool.submit(service.continue_budget, eid, 1,
                             expected_head=terminal["journal_head"])
        try:
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            service._launch.assert_not_called()
            assert len(harness.store.list_episodes()) == 1
        finally:
            holder.communicate("release\n", timeout=5)
        child = future.result(timeout=5)
    assert len(child) == 32
    assert harness.store.manifest(child)["parent_episode_id"] == eid
    service._launch.assert_called_once()
