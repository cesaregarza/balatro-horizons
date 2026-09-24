"""Interrupted journals, duplicate admissions and refused lineage boundaries."""

import fcntl
from unittest.mock import Mock

import pytest
import test_campaign_budget
from test_restore_unfinished import finish, interrupted, request_for

from balatro_horizons.service_restore import restore_admission, restore_preview, start_restore
from balatro_horizons.workbench.restoration import prepare_restore

harness = test_campaign_budget.harness


def unfinished(h, monkeypatch):
    missing_terminal = Mock()
    h.failures.append(True)
    with monkeypatch.context() as interrupted_finish:
        interrupted_finish.setattr(h.store, "finish", missing_terminal)
        h.service().execute(h.config, "luna", "PRIVATE_INTERRUPTION_FIXTURE", offline=True)
    h.failures.clear()
    missing_terminal.assert_called_once()
    parent, summary = missing_terminal.call_args.args
    assert summary["reason"] == "PROVIDER_TRANSPORT_UNKNOWN"
    assert h.store.summary(parent) is None
    return parent


def test_unterminated_run_with_safe_checkpoint_is_restored_without_rewriting_it(harness, monkeypatch):
    h = harness
    parent = unfinished(h, monkeypatch)
    before = (h.store.episode_path(parent) / "events.jsonl").read_bytes()
    plan = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    service = h.service()
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert h.store.summary(parent) is None
    assert (h.store.episode_path(parent) / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("committed", [False, True])
def test_unknown_or_committed_action_after_latest_boundary_is_not_resent(harness, monkeypatch, committed):
    h = harness
    parent = unfinished(h, monkeypatch)
    h.store.append(parent, "action_intent", {}, observation_id=0, request_id="a" * 32)
    if committed:
        h.store.append(parent, "action_commit", {}, observation_id=0, request_id="a" * 32)
    before = len(h.calls)
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == "RESTORE_UNSETTLED_ACTION"
    assert len(h.calls) == before and len(h.games) == 1


def test_missing_latest_checkpoint_never_silently_rewinds(harness):
    h = harness
    parent = interrupted(h)
    (h.store.episode_path(parent, True) / "checkpoint-0.json").unlink()
    assert not restore_preview(h.service(), parent, paid_enabled=True)["available"]
    assert len(h.games) == 1


def test_duplicate_and_foreign_worker_admission_is_refused_before_child(harness):
    h = harness
    parent = interrupted(h)
    plan = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    service = h.service()
    service._launch = Mock()
    with restore_admission(h.store, parent):
        with pytest.raises(ValueError, match="WORKER_BUSY"):
            start_restore(service, parent, request_for(plan), paid_enabled=True)
    with (h.store.root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="WORKER_BUSY"):
            start_restore(service, parent, request_for(plan), paid_enabled=True)
    assert len(h.store.list_episodes()) == 1
    start_restore(service, parent, request_for(plan), paid_enabled=True)
    with pytest.raises(ValueError, match="RESTORE_CONTINUATION_UNFINISHED"):
        start_restore(h.service(), parent, request_for(plan), paid_enabled=True)
    assert len(h.store.list_episodes()) == 2
    service._launch.assert_called_once()


def test_pre_run_failure_keeps_shared_spending_and_allows_funded_retry(harness, monkeypatch):
    from balatro_horizons import service as service_module

    h = harness
    parent = interrupted(h)
    plan = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    with monkeypatch.context() as failed:
        failed.setattr(service_module, "FakeGame", Mock(side_effect=ValueError("FIXTURE_START_FAILED")))
        service = h.service()
        child = start_restore(service, parent, request_for(plan), paid_enabled=True)
        service.thread.join(5)
    assert not service.thread.is_alive()
    assert h.store.summary(child)["cost_usd"] == 0
    retry = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    assert retry["costs"] == plan["costs"] and retry["plan_hash"] != plan["plan_hash"]
    next_service = h.service()
    next_child = start_restore(next_service, parent, request_for(retry), paid_enabled=True)
    finish(next_service)
    assert h.store.summary(next_child)["outcome"] == "WIN"
