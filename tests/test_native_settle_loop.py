"""Frame-by-frame characterization of the dispatcher settle loop."""

from native_dispatch_harness import NativeHarness


def park_action(harness):
    response = harness.request("bh_action", {"action": "skip_pack"}, request_id=1)
    assert response is None
    assert harness.eval("action_count()") == 1


def test_pending_action_never_resolves_during_first_thirty_frames():
    harness = NativeHarness()
    park_action(harness)
    harness.advance(30)
    assert harness.eval("response_count()") == 0


def test_pending_action_resolves_on_tenth_stable_frame_after_age_thirty():
    harness = NativeHarness()
    park_action(harness)
    harness.advance(39)
    assert harness.eval("response_count()") == 0
    harness.advance(1)
    assert harness.eval("response_count()") == 1


def test_not_ready_frame_resets_accumulated_stability():
    harness = NativeHarness()
    park_action(harness)
    harness.advance(35)
    harness.execute("G.STATE_COMPLETE=false")
    harness.advance(1)
    harness.execute("G.STATE_COMPLETE=true")
    harness.advance(9)
    assert harness.eval("response_count()") == 0
    harness.advance(1)
    assert harness.eval("response_count()") == 1


def test_resolved_action_sends_fresh_inspection_output():
    harness = NativeHarness()
    park_action(harness)
    harness.advance(40)
    response = harness.globals.responses[1]
    assert response.marker == "INSPECTED_STATE"
    assert response.bh.ready is True
