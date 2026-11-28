"""Teardown's action-log path: a completed destroy for the ID, started no earlier than the
run's creation less the clock-skew allowance, proves the deletion. A 404 and a listing
that omits the ID must still follow, and nothing else in the log counts."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from cloud_support import Cloud, DigitalOceanStub, page_across_the_cutoff, page_of

from balatro_horizons.cloud.digitalocean import DigitalOceanClient
from balatro_horizons.cloud.ledger import Ledger

SKEW = timedelta(minutes=5)


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def deleted_outside(cloud, status="completed", started=None, logged_id=None):
    """Delete the run's Droplet behind the controller's back; the log shows a destroy."""
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.inventory.pop(droplet_id)
    log = getattr(cloud.provider, "log_destroy", None)
    assert log is not None, "the fake provider keeps an action log of destroys"
    log(logged_id or droplet_id, status=status, at=started)
    return droplet_id


def assert_held(cloud):
    record = Ledger(cloud.root).get("r1")
    assert (record.state, record.verified_at, record.settled_usd) == (
        "cleanup_unverified", None, None)
    assert "CLEANUP_UNVERIFIED" in cloud.alert_codes()
    return record


def test_a_droplet_deleted_outside_the_controller_clears_on_its_destroy(cloud):
    controller = cloud.launched()
    deleted_outside(cloud)
    controller.cancel("r1", now=True)
    record = cloud.assert_torn_down("r1", "cancelled")
    assert cloud.provider.deleted() == []
    resource = record.resources[0]
    assert resource.acknowledged_at is None
    assert (resource.destroy_evidence.status, resource.destroy_evidence.resource_id) == (
        "completed", resource.id)


def test_a_destroy_at_the_edge_of_the_clock_skew_allowance_still_counts(cloud):
    controller = cloud.launched()
    deleted_outside(cloud, started=cloud.ledger.get("r1").created_at - SKEW)
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")


@pytest.mark.parametrize("case", ["no destroy", "another ID", "errored", "in progress",
                                  "before the run"])
def test_nothing_else_in_the_log_clears_a_droplet_that_reads_404(cloud, case):
    controller = cloud.launched()
    created = cloud.ledger.get("r1").created_at
    if case == "no destroy":
        cloud.provider.inventory.pop(cloud.owned_ids("r1")[0])
    else:
        deleted_outside(cloud, **{
            "another ID": {"logged_id": cloud.unrelated[0].id},
            "errored": {"status": "errored"},
            "in progress": {"status": "in-progress"},
            "before the run": {"started": created - SKEW - timedelta(seconds=1)},
        }[case])
    controller.cancel("r1", now=True)
    cloud.reconcile()
    controller.tick()
    record = assert_held(cloud)
    assert cloud.provider.deleted() == []
    # The log was read, and what it showed is on the record.
    assert getattr(record.resources[0], "destroy_checked_at", None) is not None


def test_evidence_that_names_another_id_never_counts(cloud):
    controller = cloud.launched()
    cloud.provider.inventory.pop(cloud.owned_ids("r1")[0])
    controller.cancel("r1", now=True)
    record = assert_held(cloud)
    resource = record.resources[0]
    assert "destroy_evidence" in type(resource).model_fields
    evidence = {"action_id": 1, "status": "completed", "started_at": record.created_at}

    def with_evidence(resource_id):
        changed = resource.model_dump() | {
            "destroy_evidence": evidence | {"resource_id": resource_id}}
        return record.model_copy(update={"resources": [resource.model_validate(changed)]})

    teardown = cloud.controller().teardown
    assert teardown.verified_absent(with_evidence(resource.id + 1)) is False
    assert teardown.verified_absent(with_evidence(resource.id)) is True


@pytest.mark.parametrize("later", ["errored", "in-progress"])
def test_completed_evidence_outlasts_a_later_lookup_that_shows_less(cloud, later):
    controller = cloud.launched()
    droplet_id = deleted_outside(cloud)
    record = cloud.ledger.get("r1")
    controller.teardown.corroborate(record, lambda: 60)
    assert record.resources[0].destroy_evidence.status == "completed"
    # The log no longer lists the completed destroy, only a lesser action for the same ID.
    cloud.provider.destroys.clear()
    cloud.provider.log_destroy(droplet_id, status=later)
    controller.teardown.corroborate(record, lambda: 60)
    assert record.resources[0].destroy_evidence.status == "completed"
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")


@pytest.mark.parametrize("later", ["failed", "timed_out", "late"])
def test_completed_evidence_outlasts_a_later_lookup_that_fails_or_runs_out(cloud, later):
    controller = cloud.launched()
    deleted_outside(cloud)
    reads = []
    read = cloud.provider.list_destroy_actions

    def first_in_time(**kwargs):
        reads.append(cloud.clock())
        actions = read(**kwargs)
        if later == "late" and len(reads) > 1:
            # It returns, but only after its deadline, as one decoded slowly would.
            cloud.clock.advance(kwargs["time_left"]() + 1)
        # Every read after this one fails, or takes 50 pages at 30 seconds each.
        if later == "failed":
            cloud.provider.action_failures = 10**9
        elif later == "timed_out":
            cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = 50, 30
        return actions

    cloud.provider.list_destroy_actions = first_in_time
    controller.cancel("r1", now=True)
    resource = cloud.assert_torn_down("r1", "cancelled").resources[0]
    # A read that returns after its deadline has run out, as one cut off has.
    check = "timed_out" if later == "late" else later
    assert (len(reads), resource.destroy_evidence.status,
            getattr(resource, "destroy_check", None)) == (2, "completed", check)


def test_an_id_the_controller_may_not_delete_never_clears_on_a_destroy(cloud):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.inventory[droplet_id] = replace(cloud.provider.inventory[droplet_id], tags=())
    controller.cancel("r1", now=True)
    cloud.provider.inventory.pop(droplet_id)
    log = getattr(cloud.provider, "log_destroy", None)
    assert log is not None
    log(droplet_id)
    cloud.reconcile()
    controller.tick()
    record = assert_held(cloud)
    assert droplet_id in record.released


def test_every_teardown_records_what_the_action_log_showed(cloud):
    controller = cloud.launched()
    cloud.channel.finish("r1")
    controller.tick()
    resource = cloud.assert_torn_down("r1", "succeeded").resources[0]
    assert getattr(resource, "destroy_checked_at", None) is not None
    assert resource.delete_requested_at <= resource.acknowledged_at
    assert resource.destroy_evidence.status == "completed"


def test_a_failed_action_log_read_never_delays_settling(cloud):
    controller = cloud.launched()
    cloud.provider.action_failures = 10
    cloud.channel.finish("r1")
    controller.tick()
    resource = cloud.assert_torn_down("r1", "succeeded").resources[0]
    assert "destroys" in [call[0] for call in cloud.provider.calls]
    assert resource.destroy_evidence is None


def test_each_delete_attempt_is_recorded_before_it_is_sent(cloud):
    controller = cloud.launched()
    cloud.provider.delete_failures = 1
    controller.cancel("r1", now=True)
    resource = cloud.assert_torn_down("r1", "cancelled").resources[0]
    deletes = [call for call in cloud.provider.calls if call[0] == "delete"]
    assert len(deletes) == 2
    # The fake provider refuses any DELETE the ledger on disk does not already show.
    assert getattr(resource, "delete_requested_at", None) == resource.acknowledged_at


def test_a_delete_answered_with_a_server_error_clears_on_the_logged_destroy(tmp_path):
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "cloud")
    stub.clock = cloud.clock
    cloud.provider = DigitalOceanClient("test-token", transport=httpx.MockTransport(stub),
                                        sleep=cloud.clock.advance)
    controller = cloud.launched()
    # The provider carried out the DELETE, but the answer was lost to a 500.
    stub.delete_status = 500
    controller.cancel("r1", now=True)
    record = Ledger(cloud.root).get("r1")
    assert (record.state, record.teardown_reason) == ("torn_down", "cancelled")
    assert stub.droplets == {}
    assert record.resources[0].acknowledged_at is None


def test_a_lost_answer_whose_destroy_sits_past_a_page_crossing_the_cutoff_stays_held(tmp_path):
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "cloud")
    stub.clock = cloud.clock
    cloud.provider = DigitalOceanClient("test-token", transport=httpx.MockTransport(stub),
                                        sleep=cloud.clock.advance)
    controller = cloud.launched()
    launched = cloud.ledger.get("r1")
    # Page 1 is neatly sorted and crosses the cutoff. The rest of the log, our destroy
    # included, follows on page 2, newer than page 1's last entry.
    stub.action_pages = lambda log: [page_across_the_cutoff(), log]
    # The provider carried out the DELETE, but the answer was lost to a 500. The Droplet
    # then reads 404 and is in no listing.
    stub.delete_status = 500
    controller.cancel("r1", now=True)
    cloud.clock.advance(3600)
    controller.tick()
    cloud.reconcile()
    record = Ledger(cloud.root).get("r1")
    resource = record.resources[0]
    assert (record.state, [r.id for r in record.resources], record.released,
            record.reserved_usd) == ("cleanup_unverified", [launched.resources[0].id], [],
                                     launched.reserved_usd)
    assert (resource.delete_requested_at is not None, resource.acknowledged_at) == (True, None)
    # Every read stopped after page 1, and a miss proves nothing.
    assert (resource.destroy_check, resource.destroy_evidence) == ("unknown", None)
    reads = [r for r in stub.requests if r.url.path == "/v2/actions"]
    assert {r.url.params["page"] for r in reads} == {"1"}
    # Every DELETE went out before the log was first read, so none followed a miss.
    assert "DELETE" not in [r.method for r in stub.requests[stub.requests.index(reads[0]):]]


def unmatched_ids(cloud):
    return [watch.droplet_id for watch in Ledger(cloud.root).unmatched()]


def lost_answer(cloud, logged):
    """Cancel a run whose DELETE the provider accepts, though the answer is lost. The
    Droplet then reads back untagged twice while it is destroyed, then 404. The log shows
    its destroy only when `logged`."""
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.partial_delete, cloud.provider.destroy_reads = True, 2
    cloud.provider.log_destroys = logged
    controller.cancel("r1", now=True)
    record = Ledger(cloud.root).get("r1")
    # Nothing is given up, and the one DELETE went out while the Droplet read with both tags.
    assert (record.released, unmatched_ids(cloud), cloud.provider.deleted()) == (
        [], [], [droplet_id])
    return droplet_id


def test_a_lost_answer_to_an_accepted_delete_clears_on_the_completed_destroy(cloud):
    lost_answer(cloud, logged=True)
    resource = cloud.assert_torn_down("r1", "cancelled").resources[0]
    assert (resource.acknowledged_at, resource.destroy_evidence.status) == (None, "completed")


def test_a_lost_answer_holds_the_id_while_the_log_shows_no_destroy(cloud):
    droplet_id = lost_answer(cloud, logged=False)
    record = assert_held(cloud)
    assert [(r.id, r.acknowledged_at, r.delete_requested_at is not None)
            for r in record.resources] == [(droplet_id, None, True)]


@pytest.mark.parametrize(("path", "state"), [("held", "cleanup_unverified"),
                                             ("lookup", "torn_down")])
def test_every_log_read_reaches_back_to_the_run_start_less_the_allowance(cloud, path, state):
    controller = cloud.launched()
    if path == "held":
        # The DELETE's answer is lost and the log never shows the destroy, so every pass
        # reads the log, and the run never settles to look its ID up.
        cloud.provider.partial_delete, cloud.provider.log_destroys = True, False
    controller.cancel("r1", now=True)
    record = Ledger(cloud.root).get("r1")
    reads = [call[1] for call in cloud.provider.calls if call[0] == "destroys"]
    assert (record.state, reads) == (state, [record.created_at - SKEW] * len(reads))
    # An acknowledged run reads the log once, after settling.
    assert len(reads) > 1 if path == "held" else len(reads) == 1


class DecodedSlowly(httpx.Response):
    """A page answered at once whose decoding takes `seconds` on the fake clock."""

    def __init__(self, page, clock, seconds):
        super().__init__(page.status_code, json=page.json())
        self.clock, self.seconds = clock, seconds

    def json(self, **kwargs):
        self.clock.advance(self.seconds)
        return super().json(**kwargs)


@pytest.mark.parametrize(("past_the_window", "expected"), [
    (1, ("cleanup_unverified", "timed_out", None)),
    (-1, ("torn_down", "found", "completed")),
], ids=["late", "on_time"])
def test_a_held_run_clears_only_on_a_log_page_decoded_in_time(tmp_path, past_the_window,
                                                              expected):
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "cloud")
    stub.clock = cloud.clock
    log = {"shown": True, "decoding": 0}

    def answer(request):
        page = stub(request)
        if request.url.path != "/v2/actions":
            return page
        shown = page if log["shown"] else page_of("actions", [])
        return DecodedSlowly(shown, cloud.clock, log["decoding"])

    cloud.provider = DigitalOceanClient("test-token", transport=httpx.MockTransport(answer),
                                        sleep=cloud.clock.advance)
    controller = cloud.launched()
    # The provider carried out the DELETE, but the answer was lost to a 500, and the log
    # showed nothing within the pass, so the run is held.
    stub.delete_status, log["shown"] = 500, False
    controller.cancel("r1", now=True)
    assert_held(cloud)
    # The next pass gets the log's last page at once, and decoding it ends just past the
    # pass's window, or just within it.
    log.update(shown=True, decoding=cloud.settings.cleanup_verify_seconds + past_the_window)
    controller.tick()
    record = Ledger(cloud.root).get("r1")
    resource = record.resources[0]
    status = resource.destroy_evidence and resource.destroy_evidence.status
    assert (record.state, resource.destroy_check, status, resource.destroy_checked_at) == (
        *expected, cloud.clock())
    assert resource.acknowledged_at is None


def test_an_acknowledged_teardown_settles_before_a_slow_action_log_is_read(cloud):
    controller = cloud.launched()
    # Half an hour at 0.1 an hour, then a log of 50 pages at 30 seconds each.
    cloud.clock.advance(1800)
    cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = 50, 30
    reads = []
    read = cloud.provider.list_destroy_actions

    def noted(**kwargs):
        reads.append((cloud.clock(), Ledger(cloud.root).get("r1")))
        return read(**kwargs)

    cloud.provider.list_destroy_actions = noted
    cancelled_at = cloud.clock()
    controller.cancel("r1", now=True)
    record = cloud.assert_torn_down("r1", "cancelled")
    assert (record.verified_at, record.settled_usd) == (cancelled_at, 0.05)
    # The one read came once the run had settled, and changed nothing settled.
    assert [(at, before.state, before.verified_at) for at, before in reads] == [
        (cancelled_at, "torn_down", cancelled_at)]
    settled = reads[0][1]
    assert record.model_dump(exclude={"resources"}) == settled.model_dump(exclude={"resources"})
    # It ran out at its deadline, a minute on, and recorded when.
    resource, deadline = record.resources[0], cancelled_at + timedelta(seconds=60)
    assert (getattr(resource, "destroy_check", None), resource.destroy_checked_at,
            cloud.clock()) == ("timed_out", deadline, deadline)


@pytest.mark.parametrize("outcome", ["found", "unknown", "failed", "timed_out"])
def test_the_lookup_after_settling_records_how_it_ended_and_when(cloud, outcome):
    controller = cloud.launched()
    cloud.provider.log_destroys = outcome != "unknown"
    cloud.provider.action_failures = int(outcome == "failed")
    if outcome == "timed_out":
        # Three pages at 30 seconds each need more than the lookup's minute.
        cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = 3, 30
    controller.cancel("r1", now=True)
    record = cloud.assert_torn_down("r1", "cancelled")
    resource = record.resources[0]
    ended = record.verified_at + timedelta(seconds=60 if outcome == "timed_out" else 0)
    status = resource.destroy_evidence and resource.destroy_evidence.status
    assert (getattr(resource, "destroy_check", None), resource.destroy_checked_at, status) == (
        outcome, ended, "completed" if outcome == "found" else None)
