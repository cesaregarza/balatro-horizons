"""A create with no known outcome is held on every teardown path, never settled empty.

The create was accepted, the controller died before it recorded the Droplet ID, and
neither the listings nor the action log show the Droplet yet. Each test reaches the
run first through one teardown entry point, on a fresh ledger and controller over
the same directory, before any controller recovery.
"""

import httpx
import pytest
from cloud_support import Cloud, DigitalOceanStub

from balatro_horizons.cloud.controller import Controller
from balatro_horizons.cloud.digitalocean import DigitalOceanClient
from balatro_horizons.cloud.fake import SimulatedCrash
from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.reconcile import reconcile

NEVER = 10**9
HOUR = 3600


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def unseen_create(cloud):
    """Crash right after the provider accepts the create; nothing reveals the Droplet."""
    cloud.provider.listing_lag = NEVER
    cloud.provider.pending_creates = True
    cloud.provider.crash_at.add("after_create")
    with pytest.raises(SimulatedCrash):
        cloud.controller().launch("r1")
    assert len(cloud.owned_ids("r1")) == 1
    return cloud.restarted()


def reserved_remaining(cloud):
    runs = Ledger(cloud.root).runs()
    spent = sum(r.settled_usd if r.state == "torn_down" else r.reserved_usd for r in runs)
    return cloud.settings.infrastructure_budget_usd - spent


def assert_held(cloud):
    """Same reservation and slot, the gate closed, the Droplet kept, no second create."""
    record = Ledger(cloud.root).get("r1")
    assert record.state == "create_unresolved"
    assert (record.verified_at, record.settled_usd) == (None, None)
    assert reserved_remaining(cloud) == pytest.approx(1.0 - cloud.settings.worst_case_usd)
    refused = cloud.restarted().launch("r2")
    assert {"CREATE_UNRESOLVED", "CONCURRENCY_LIMIT"} <= set(refused["reasons"])
    assert len(cloud.creates()) == 1
    assert len(cloud.owned_ids("r1")) == 1
    assert cloud.provider.deleted() == []


def assert_recovers_only_on_sight(cloud, reason):
    """Recovery keeps holding; the Droplet coming into view is what releases the run."""
    restarted = cloud.restarted()
    restarted.recover()
    assert_held(cloud)
    cloud.provider.hidden.clear()
    restarted.tick()
    cloud.assert_torn_down("r1", reason)
    assert len(cloud.provider.deleted()) == 1
    assert len(cloud.creates()) == 1


def test_reconcile_after_the_boot_timeout_holds_an_unseen_create(cloud):
    unseen_create(cloud)
    cloud.clock.advance(cloud.settings.boot_timeout_seconds + 1)
    cloud.reconcile()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "boot_failed")


def test_reconcile_after_the_deadline_holds_an_unseen_create(cloud):
    unseen_create(cloud)
    cloud.clock.advance(cloud.settings.max_run_hours * HOUR + 1)
    cloud.reconcile()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "deadline")


def test_cancel_request_then_reconcile_holds_an_unseen_create(cloud):
    unseen_create(cloud).cancel("r1")
    cloud.reconcile()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "cancelled")


def test_cancel_request_then_a_tick_holds_an_unseen_create(cloud):
    controller = unseen_create(cloud)
    controller.cancel("r1")
    controller.tick()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "cancelled")


def test_cancel_now_holds_an_unseen_create(cloud):
    assert unseen_create(cloud).cancel("r1", now=True)["state"] == "create_unresolved"
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "cancelled")


def test_deadline_on_a_fresh_controller_tick_holds_an_unseen_create(cloud):
    controller = unseen_create(cloud)
    cloud.clock.advance(cloud.settings.max_run_hours * HOUR + 1)
    controller.tick()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "deadline")


def test_boot_check_on_a_fresh_controller_tick_holds_an_unseen_create(cloud):
    unseen_create(cloud).tick()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "boot_failed")


def test_controller_error_holds_an_unseen_create(cloud, monkeypatch):
    controller = unseen_create(cloud)

    def failing_tick():
        raise RuntimeError("supervision failed")

    monkeypatch.setattr(controller, "tick", failing_tick)
    with pytest.raises(RuntimeError):
        controller.supervise()
    assert_held(cloud)
    assert_recovers_only_on_sight(cloud, "controller_error")


def http_controller(tmp_path, actions, droplets=(), unlisted=()):
    """A controller over the real client and a fake HTTP API whose create answers 500."""
    stub = DigitalOceanStub()
    stub.create_status = 500
    stub.actions = list(actions)
    stub.droplets = {d["id"]: d for d in droplets}
    stub.unlisted = set(unlisted)
    cloud = Cloud(tmp_path / "cloud")
    client = DigitalOceanClient("test-token", transport=httpx.MockTransport(stub),
                                sleep=cloud.clock.advance)
    controller = Controller(cloud.ledger, client, cloud.settings, clock=cloud.clock,
                            sleep=cloud.clock.advance)
    controller.launch("r1")
    return cloud, controller, stub, client


def create_action(resource_id, status):
    return {"id": 9001, "type": "create", "resource_type": "droplet",
            "resource_id": resource_id, "status": status,
            "started_at": "2026-11-24T12:00:00Z"}


def assert_still_held_long_after(cloud, controller, stub, client):
    cloud.clock.advance(HOUR)
    controller.tick()
    reconcile(cloud.ledger, client, cloud.settings, clock=cloud.clock,
              sleep=cloud.clock.advance, wait_seconds=0)
    record = cloud.ledger.get("r1")
    assert record.state == "create_unresolved"
    assert (record.verified_at, record.settled_usd) == (None, None)
    assert "CREATE_UNRESOLVED" in controller.launch("r2")["reasons"]
    methods = [r.method for r in stub.requests]
    assert methods.count("POST") == 1 and "DELETE" not in methods


def test_in_progress_create_with_no_resource_id_keeps_the_run_unresolved(tmp_path):
    held = http_controller(tmp_path, [create_action(None, "in-progress")])
    assert_still_held_long_after(*held)


def test_in_progress_create_whose_id_returns_404_keeps_the_run_unresolved(tmp_path):
    held = http_controller(tmp_path, [create_action(7777, "in-progress")])
    assert_still_held_long_after(*held)


def test_all_empty_reads_long_past_the_create_never_clear_it(tmp_path):
    held = http_controller(tmp_path, [])
    assert_still_held_long_after(*held)


def test_completed_create_naming_our_droplet_is_deleted_and_verified(tmp_path):
    ours = {"id": 7777, "name": "bh-r1", "status": "active", "created_at": "2026-11-24T12:00:00Z",
            "tags": ["bh-owner:test-ctl", "bh-run:r1"]}
    # The by-ID read sees it even while every listing lags behind.
    cloud, controller, stub, _ = http_controller(
        tmp_path, [create_action(7777, "completed")], [ours], unlisted={7777})
    controller.tick()
    record = cloud.ledger.get("r1")
    assert (record.state, record.teardown_reason) == ("torn_down", "create_failed")
    assert 7777 not in stub.droplets
    assert [r.method for r in stub.requests].count("DELETE") == 1
