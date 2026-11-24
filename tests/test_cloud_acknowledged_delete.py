"""A Droplet is gone only after the provider acknowledged our DELETE for it.

No read-after-write guarantee is assumed: before an acknowledgment, a 404 by ID or
an empty listing proves nothing, and the run keeps its reservation and its slot.
"""

from dataclasses import replace

import httpx
import pytest
from cloud_support import Cloud, DigitalOceanStub

from balatro_horizons.cloud.controller import Controller
from balatro_horizons.cloud.digitalocean import DigitalOceanClient
from balatro_horizons.cloud.fake import SimulatedCrash
from balatro_horizons.cloud.ledger import Ledger


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def reserved_remaining(cloud):
    runs = Ledger(cloud.root).runs()
    spent = sum(r.settled_usd if r.state == "torn_down" else r.reserved_usd for r in runs)
    return cloud.settings.infrastructure_budget_usd - spent


def assert_unsettled(cloud, state="cleanup_unverified"):
    record = Ledger(cloud.root).get("r1")
    assert record.state == state
    assert (record.verified_at, record.settled_usd) == (None, None)
    assert reserved_remaining(cloud) == pytest.approx(1.0 - cloud.settings.worst_case_usd)
    creates = len(cloud.creates())
    assert cloud.restarted().launch("r2")["outcome"] == "refused"
    assert len(cloud.creates()) == creates
    return record


def crash_between_ownership_writes(cloud, monkeypatch, droplet_id):
    """Crash at the second of the two writes that hand an ID to the unmatched watch."""
    first = []
    save, note = cloud.ledger.save, cloud.ledger.note_unmatched

    def step(kind):
        if first and first[0] != kind:
            raise SimulatedCrash("between the ownership-loss writes")
        first.append(kind)

    def saving(record):
        if droplet_id in record.released:
            step("release")
        save(record)

    def noting(noted_id, now):
        if noted_id == droplet_id:
            step("watch")
        note(noted_id, now)

    monkeypatch.setattr(cloud.ledger, "save", saving)
    monkeypatch.setattr(cloud.ledger, "note_unmatched", noting)


def test_crash_between_the_ownership_loss_writes_keeps_the_id_held(cloud, monkeypatch):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    # Both tags are gone, so neither listing can rediscover it.
    cloud.provider.inventory[droplet_id] = replace(cloud.provider.inventory[droplet_id], tags=())
    crash_between_ownership_writes(cloud, monkeypatch, droplet_id)
    with pytest.raises(SimulatedCrash):
        controller.cancel("r1", now=True)
    monkeypatch.undo()
    cloud.restarted().recover()
    record = assert_unsettled(cloud)
    assert droplet_id in record.resource_ids() | set(record.released)
    assert droplet_id in [watch.droplet_id for watch in Ledger(cloud.root).unmatched()]
    assert droplet_id in cloud.provider.inventory
    assert droplet_id not in cloud.provider.deleted()


def test_known_id_that_reads_404_before_any_delete_leaves_the_run_unsettled(cloud):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    # Deleted outside the controller, or a by-ID read that lags behind the provider.
    cloud.provider.inventory.pop(droplet_id)
    controller.cancel("r1", now=True)
    assert_unsettled(cloud)
    assert cloud.provider.deleted() == []
    assert "CLEANUP_UNVERIFIED" in [alert.code for alert in cloud.ledger.alerts()]


def http_cloud(tmp_path):
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "cloud")
    client = DigitalOceanClient("test-token", transport=httpx.MockTransport(stub),
                                sleep=cloud.clock.advance)
    controller = Controller(cloud.ledger, client, cloud.settings, clock=cloud.clock,
                            sleep=cloud.clock.advance)
    return cloud, controller, stub


def methods(stub, method):
    return [r for r in stub.requests if r.method == method]


def test_name_sighting_then_404s_then_visible_again_holds_until_an_acknowledged_delete(
    tmp_path,
):
    cloud, controller, stub = http_cloud(tmp_path)
    ours = {"id": 7777, "name": "bh-r1", "status": "active",
            "created_at": "2026-11-24T12:00:00Z", "tags": ["bh-owner:test-ctl", "bh-run:r1"]}
    stub.droplets = {7777: ours}
    stub.create_status = 500
    # The name filter sees both tags; by-ID reads answer 404 and the tag listing omits it.
    stub.tag_hidden, stub.id_hidden = {7777}, {7777}
    controller.launch("r1")
    record = cloud.ledger.get("r1")
    assert record.state in ("create_unresolved", "tearing_down", "cleanup_unverified")
    assert (record.verified_at, record.settled_usd) == (None, None)
    stub.create_status = None
    assert controller.launch("r2")["outcome"] == "refused"
    assert len(methods(stub, "POST")) == 1 and methods(stub, "DELETE") == []
    stub.tag_hidden, stub.id_hidden = set(), set()
    controller.tick()
    assert (cloud.ledger.get("r1").state, 7777 in stub.droplets) == ("torn_down", False)
    assert [r.url.path for r in methods(stub, "DELETE")] == ["/v2/droplets/7777"]
    assert controller.launch("r2")["outcome"] == "launched"


def test_delete_answered_404_is_not_an_acknowledgment(tmp_path):
    cloud, controller, stub = http_cloud(tmp_path)
    assert controller.launch("r1")["outcome"] == "launched"
    controller.tick()
    assert cloud.ledger.get("r1").state == "running"
    stub.delete_status = 404
    controller.cancel("r1", now=True)
    record = cloud.ledger.get("r1")
    assert record.state == "cleanup_unverified"
    assert (record.verified_at, record.settled_usd) == (None, None)
    assert "CLEANUP_UNVERIFIED" in [alert.code for alert in cloud.ledger.alerts()]
    assert controller.launch("r2")["outcome"] == "refused"
    assert len(methods(stub, "POST")) == 1
