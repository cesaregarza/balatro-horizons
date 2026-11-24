"""Shared offline fixtures for the cloud runner: a fake clock and a wired controller."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from balatro_horizons.cli.cloud import status_report
from balatro_horizons.cloud.controller import Controller
from balatro_horizons.cloud.fake import FakeChannel, FakeProvider
from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.reconcile import reconcile
from balatro_horizons.cloud.settings import CloudSettings, run_tag

FOREIGN_OWNER = "bh-owner:someone-else"


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def cloud_settings(**overrides):
    values = {
        "controller_id": "test-ctl", "infrastructure_budget_usd": 1.0, "max_run_hours": 2,
        "hourly_price_usd": 0.1, "boot_timeout_seconds": 300, "heartbeat_grace_seconds": 120,
        "cleanup_verify_seconds": 60, "retry_base_seconds": 1, "retry_max_seconds": 8,
        "poll_seconds": 10,
    }
    return CloudSettings(**values | overrides)


@dataclass
class Cloud:
    root: object
    settings: CloudSettings = field(default_factory=cloud_settings)
    provider: FakeProvider = field(default_factory=FakeProvider)
    channel: FakeChannel = field(default_factory=FakeChannel)
    clock: FakeClock = field(default_factory=FakeClock)

    def __post_init__(self):
        self.ledger = Ledger(self.root)
        # The fake provider stamps creation times and its action log from this clock.
        self.provider.clock = self.clock
        self.unrelated = [
            self.provider.seed(),
            self.provider.seed("web", "production"),
            self.provider.seed(FOREIGN_OWNER, run_tag("r1")),
            self.provider.seed(FOREIGN_OWNER),
            self.provider.seed(run_tag("r1")),
        ]

    def controller(self):
        return Controller(self.ledger, self.provider, self.settings, channel=self.channel,
                          clock=self.clock, sleep=self.clock.advance)

    def restarted(self):
        """A fresh controller over a fresh ledger object on the same directory."""
        return Controller(Ledger(self.root), self.provider, self.settings, channel=self.channel,
                          clock=self.clock, sleep=self.clock.advance)

    def creates(self):
        return [call for call in self.provider.calls if call[0] == "create"]

    def reconcile(self):
        return reconcile(self.ledger, self.provider, self.settings, clock=self.clock,
                         sleep=self.clock.advance, wait_seconds=0)

    def active_alerts(self):
        """The alerts status shows, read from a fresh ledger on the same directory."""
        return status_report(Ledger(self.root), self.settings, self.clock())["alerts"]

    def alert_codes(self):
        return [alert["code"] for alert in self.active_alerts()]

    def owned_ids(self, run_id):
        tags = {self.settings.owner_tag, run_tag(run_id)}
        return [d.id for d in self.provider.inventory.values() if tags <= set(d.tags)]

    def launched(self, run_id="r1"):
        """Launch and boot one run to the running state."""
        controller = self.controller()
        assert controller.launch(run_id)["outcome"] == "launched"
        controller.tick()
        assert self.ledger.get(run_id).state == "running"
        return controller

    def assert_unrelated_survive(self):
        for droplet in self.unrelated:
            assert self.provider.inventory.get(droplet.id) == droplet
        assert not {d.id for d in self.unrelated} & set(self.provider.deleted())

    def assert_torn_down(self, run_id, reason):
        record = self.ledger.get(run_id)
        assert (record.state, record.teardown_reason) == ("torn_down", reason)
        assert record.verified_at is not None and record.settled_usd is not None
        assert self.owned_ids(run_id) == []
        self.assert_unrelated_survive()
        return record


class DigitalOceanStub:
    """Answers the Droplet endpoints in memory, as an httpx mock transport handler."""

    def __init__(self):
        self.droplets = {}
        self.actions = []
        self.requests = []
        # A status here answers every create with that error and makes nothing.
        self.create_status = None
        # IDs kept out of the tag and name listings, as a lagging index would.
        self.unlisted = set()
        # IDs kept out of the tag listings only, while the name filter still sees them.
        self.tag_hidden = set()
        # IDs that answer 404 by ID, though the Droplet still exists.
        self.id_hidden = set()
        # A status here answers a DELETE with it; the Droplet is removed regardless.
        self.delete_status = None

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/v2")
        if path == "/droplets" and request.method == "POST":
            return self.create(json.loads(request.content))
        if path == "/actions":
            return page_of("actions", self.actions)
        if path == "/droplets":
            params = request.url.params
            listed = [d for d in self.droplets.values() if d["id"] not in self.unlisted]
            if "name" in params:
                found = [d for d in listed if d["name"].lower() == params["name"].lower()]
            else:
                found = [d for d in listed if params["tag_name"] in d["tags"]
                         and d["id"] not in self.tag_hidden]
            return page_of("droplets", found)
        droplet_id = int(path.rsplit("/", 1)[1])
        if droplet_id not in self.droplets or droplet_id in self.id_hidden:
            return httpx.Response(404, json={"id": "not_found", "message": "gone"})
        if request.method == "DELETE":
            del self.droplets[droplet_id]
            return httpx.Response(self.delete_status or 204)
        return httpx.Response(200, json={"droplet": self.droplets[droplet_id]})

    def create(self, body):
        if self.create_status is not None:
            return httpx.Response(self.create_status, json={"id": "server_error"})
        droplet_id = 5000 + len(self.requests)
        self.droplets[droplet_id] = {"id": droplet_id, "name": body["name"], "status": "active",
                                     "tags": body["tags"], "created_at": "2026-11-24T12:00:00Z"}
        self.actions.append({"id": droplet_id + 1, "type": "create", "resource_type": "droplet",
                             "resource_id": droplet_id, "status": "completed",
                             "started_at": "2026-11-24T12:00:00Z"})
        return httpx.Response(202, json={"droplet": self.droplets[droplet_id]})


def page_of(key, items):
    return httpx.Response(200, json={key: items, "links": {}, "meta": {"total": len(items)}})
