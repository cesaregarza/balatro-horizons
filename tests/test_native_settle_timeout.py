"""A permanently unready native write must not hold the dispatcher forever."""

import pytest
from native_dispatch_harness import NativeHarness


@pytest.mark.parametrize("method", ["bh_action", "select"])
def test_settle_timeout_records_unknown_and_releases_writer(method):
    harness = NativeHarness()
    params = {"action": "skip_pack"} if method == "bh_action" else {"blind": "Small"}
    assert harness.request(method, params, request_id=1) is None
    harness.execute("G.STATE_COMPLETE=false")

    harness.advance(899)
    assert harness.status(1).status == "pending"
    assert harness.request("select", {"blind": "Big"}, request_id=2).message == "BUSY"

    harness.advance(1)
    timeout = harness.globals.responses[harness.eval("response_count()")]
    assert timeout.message == "ACTION_STATUS_UNKNOWN"
    assert harness.globals.persist_count == 2
    assert harness.status(1).status == "unknown"
    assert harness.status(1).response is None
    assert harness.request(method, params, request_id=1).message == "ACTION_STATUS_UNKNOWN"
    assert harness.request("bh_inspect", request_id=3).bh.busy is False

    responses = harness.eval("response_count()")
    harness.advance(40)
    assert harness.eval("response_count()") == responses
    assert harness.request("select", {"blind": "Big"}, request_id=4) is None
    assert harness.status(4).status == "pending"
    harness.execute("G.STATE_COMPLETE=true")
    harness.advance(40)
    assert harness.status(4).status == "committed"
    assert harness.status(1).status == "unknown"


def test_tenth_ready_frame_at_age_cap_still_commits():
    harness = NativeHarness()
    assert harness.request("bh_action", {"action": "skip_pack"}, request_id=1) is None
    harness.execute("G.STATE_COMPLETE=false")
    harness.advance(890)
    harness.execute("G.STATE_COMPLETE=true")
    harness.advance(9)
    assert harness.status(1).status == "pending"
    harness.advance(1)
    assert harness.status(1).status == "committed"
