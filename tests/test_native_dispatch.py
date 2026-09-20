"""Offline characterization of authentication, replay, and settlement dispatch."""

import pytest
from native_dispatch_harness import NativeHarness, assert_error_unchanged


@pytest.fixture
def harness():
    return NativeHarness()


def test_authentication_rejects_wrong_token_and_strips_valid_token(harness):
    assert_error_unchanged(harness, "UNAUTHORIZED", "select", token="wrong-token")
    assert harness.eval("upstream_count()") == 0

    assert harness.request("select", request_id=2) is None
    assert harness.eval("upstream_count()") == 1
    assert harness.globals.upstream_requests[1].params["_bh_token"] is None


def test_method_allowlist_rejects_unknown_method_without_mutating_game(harness):
    assert_error_unchanged(harness, "METHOD_FORBIDDEN", "private_debug")
    assert harness.eval("upstream_count()") == 0


@pytest.mark.parametrize("method", ["save", "load"])
def test_checkpoint_path_guard_rejects_outside_paths(harness, method):
    assert_error_unchanged(harness, "PATH_FORBIDDEN", method, {"path": "/tmp/save.jkr"})
    assert harness.eval("upstream_count()") == 0


@pytest.mark.parametrize(
    "method,params",
    [
        ("bh_inspect", {}),
        ("bh_request_status", {"request_id": "missing"}),
        ("bh_rules", {}),
    ],
)
def test_reads_never_enter_the_ledger_or_set_busy(harness, method, params):
    before = harness.snapshot()
    assert harness.request(method, params, request_id=10) is not None
    assert harness.snapshot() == before
    assert harness.status(10).status == "unknown"

    harness.globals.auto_respond = False
    assert harness.request("select", {"blind": "Small"}, request_id=11) is None
    assert harness.status(11).status == "pending"


def test_write_while_another_write_is_pending_returns_busy(harness):
    harness.globals.auto_respond = False
    assert harness.request("select", {"blind": "Small"}, request_id=1) is None
    assert_error_unchanged(harness, "BUSY", "select", {"blind": "Big"}, request_id=2)
    assert harness.eval("upstream_count()") == 1


def test_committed_replay_uses_recorded_response_without_clearing_current_busy(harness):
    harness.execute("next_response={accepted='first'}")
    assert harness.request("select", {"blind": "Small"}, request_id=1) is None
    harness.advance(40)
    first = harness.globals.responses[harness.eval("response_count()")]
    assert first.accepted == "first"

    harness.globals.auto_respond = False
    assert harness.request("select", {"blind": "Big"}, request_id=2) is None
    forwarded = harness.eval("upstream_count()")
    replay = harness.request("select", {"blind": "Small"}, request_id=1)
    assert replay.accepted == "first"
    assert harness.eval("upstream_count()") == forwarded
    assert_error_unchanged(harness, "BUSY", "select", {"blind": "Boss"}, request_id=3)


def test_reused_request_id_with_different_intent_is_rejected(harness):
    harness.request("select", {"blind": "Small"}, request_id=1)
    assert_error_unchanged(
        harness,
        "REQUEST_ID_REUSED",
        "select",
        {"blind": "Big"},
        request_id=1,
    )


def test_replay_of_pending_request_reports_unknown_action_status(harness):
    harness.globals.auto_respond = False
    assert harness.request("select", {"blind": "Small"}, request_id=1) is None
    assert_error_unchanged(
        harness,
        "ACTION_STATUS_UNKNOWN",
        "select",
        {"blind": "Small"},
        request_id=1,
    )


def test_read_while_write_is_pending_does_not_clobber_pending_record(harness):
    harness.globals.auto_respond = False
    assert harness.request("select", {"blind": "Small"}, request_id=1) is None
    harness.globals.auto_respond = True
    assert harness.request("bh_inspect", request_id=2) is not None
    assert harness.status(1).status == "pending"
    assert_error_unchanged(harness, "BUSY", "select", {"blind": "Big"}, request_id=3)


def test_start_from_menu_runs_calibration_reset_once(harness):
    harness.execute("G.STATE=G.STATES.MENU; auto_respond=false")
    assert harness.request("start", request_id=1) is None
    assert harness.globals.reset_calls == 1
    assert harness.request("start", request_id=1).message == "ACTION_STATUS_UNKNOWN"
    assert harness.globals.reset_calls == 1


def test_send_response_records_outcome_persists_and_releases_writer(harness):
    harness.execute("next_response={ok=true}")
    assert harness.request("select", request_id=1) is None
    harness.advance(40)
    assert harness.globals.responses[harness.eval("response_count()")].ok is True
    committed = harness.status(1)
    assert committed.status == "committed" and committed.response.ok is True

    harness.execute("next_response={message='UPSTREAM_REJECTED'}")
    assert harness.request("select", request_id=2).message == "UPSTREAM_REJECTED"
    rejected = harness.status(2)
    assert rejected.status == "rejected"
    assert rejected.response.message == "UPSTREAM_REJECTED"
    assert harness.globals.persist_count == 4

    harness.globals.auto_respond = False
    assert harness.request("select", request_id=3) is None
    assert harness.status(3).status == "pending"


def test_current_error_name_classification_is_pinned_for_issue_8():
    # Issue #8 changes definite infrastructure errors away from NOT_ALLOWED.
    pending = NativeHarness()
    pending.globals.auto_respond = False
    pending.request("select", request_id=1)
    unknown = pending.request("select", request_id=1)

    busy = NativeHarness()
    busy.globals.auto_respond = False
    busy.request("select", request_id=1)
    busy_response = busy.request("select", request_id=2)

    not_ready = NativeHarness()
    not_ready.execute("G.STATE_COMPLETE=false")
    unavailable = not_ready.request("bh_action", {"action": "skip_pack"})

    assert unknown.message == "ACTION_STATUS_UNKNOWN"
    assert busy_response.message == "BUSY"
    assert unavailable.message == "NOT_READY"
    assert {unknown.name, busy_response.name, unavailable.name} == {"INFRASTRUCTURE_NAME"}
