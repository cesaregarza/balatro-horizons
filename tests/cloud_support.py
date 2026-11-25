"""Shared offline fixtures for the cloud runner: a fake clock, a fake SSH transport and a
wired controller. Every key, address, fingerprint and ID here is made up."""

import hashlib
import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from balatro_horizons.cli.cloud import status_report
from balatro_horizons.cloud.access import AccessSettings
from balatro_horizons.cloud.controller import Controller
from balatro_horizons.cloud.fake import FakeChannel, FakeProvider
from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.reconcile import reconcile
from balatro_horizons.cloud.settings import CloudSettings, run_tag

FOREIGN_OWNER = "bh-owner:someone-else"
FINGERPRINT = "00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff"
# A made-up firewall ID, in the UUID form the provider uses.
FIREWALL_ID = str(uuid.UUID(int=1))
OPERATOR_SOURCE = "203.0.113.7/32"
BUNDLE_NAME = "game-bundle.tar"
BUNDLE_BYTES = b"made-up bundle contents\n"
BUNDLE_SHA256 = hashlib.sha256(BUNDLE_BYTES).hexdigest()
PINNED_KEY = "ssh-ed25519 AAAAtestpinnedhostkey"
# What OpenSSH prints when it exits 255 before any command runs.
REFUSED = {
    "unreachable": b"ssh: connect to host 192.0.2.2 port 22: Connection refused\r\n",
    "host_key_mismatch": b"Host key verification failed.\r\n",
}
# The operator's files live apart from every ledger, so they never create a ledger's
# parents. The directory outlives each Cloud and goes when the test process ends.
OPERATOR_FILES = tempfile.TemporaryDirectory(prefix="bh-operator-")


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


def worker_status(state="ready", stage="ready", reason=None):
    """A status file as the worker writes it."""
    return {"stage": stage, "state": state, "reason": reason,
            "started_at": {stage: "2026-11-24T12:04:00+00:00"},
            "observed": {"kernel": "6.8.0-test"}}


class FakeTransport:
    """Stands in for OpenSSH and ssh-keygen: scripted answers, no connection, no real key."""

    def __init__(self):
        self.calls = []
        # Answers for the next SSH calls, given before any command could run.
        self.refusals = []
        # A step that fails ("failed") or times out ("timed_out") on every call.
        self.failures = {}
        # Status files the worker reports in turn, as dicts or raw bytes; the last repeats.
        self.statuses = [worker_status()]
        # On the clock, a call that times out runs to its timeout. Any other call takes its
        # step's seconds here, even past its timeout.
        self.durations = {}
        self.clock = None
        self.keygen_failure = False
        self.key_directories = []

    def run(self, argv, *, stdin=None, timeout):
        if argv[0] == "ssh-keygen":
            return self.keygen(argv)
        step = step_of(argv[-1], stdin)
        self.calls.append((step, list(argv), stdin, timeout))
        timed_out = not self.refusals and self.failures.get(step) == "timed_out"
        if self.clock is not None:
            self.clock.advance(timeout if timed_out else self.durations.get(step, 0))
        if self.refusals:
            return finished(argv, 255, stderr=REFUSED[self.refusals.pop(0)])
        if timed_out:
            raise subprocess.TimeoutExpired(argv, timeout)
        if self.failures.get(step) == "failed":
            return finished(argv, 1, stderr=b"command failed\n")
        if step != "status":
            return finished(argv, 0)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return finished(argv, 0, stdout=status if isinstance(status, bytes)
                        else json.dumps(status).encode())

    def keygen(self, argv):
        path = Path(argv[argv.index("-f") + 1])
        self.key_directories.append(path.parent)
        if self.keygen_failure:
            return finished(argv, 1)
        path.write_text("stand-in private half\n")
        path.with_name(path.name + ".pub").write_text(PINNED_KEY + " \n")
        return finished(argv, 0)

    def steps(self):
        return [call[0] for call in self.calls]


def step_of(command, stdin):
    if isinstance(stdin, Path):
        return "push_bundle"
    if isinstance(stdin, bytes):
        return "push_worker"
    if command == "true":
        return "probe"
    if command.startswith("systemd-run"):
        return "start"
    if "status.json" in command:
        return "status"
    raise AssertionError("unexpected remote command")


def finished(argv, code, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def admitting_firewall(owner_tag):
    """A firewall as the API returns it, restricted the way the preflight requires."""
    return {"id": FIREWALL_ID, "name": "workers", "tags": [owner_tag],
            "inbound_rules": [{"protocol": "tcp", "ports": "22",
                               "sources": {"addresses": [OPERATOR_SOURCE]}}],
            "outbound_rules": [{"protocol": "tcp", "ports": "all",
                                "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}}]}


def offered_size(settings, **overrides):
    """The configured size as the API lists it, offered in the region at the configured price."""
    return {"slug": settings.size, "available": True, "price_hourly": settings.hourly_price_usd,
            "price_monthly": 42.0, "regions": ["sfo3", settings.region]} | overrides


def admit_preflight(provider, settings):
    """Give the fake provider the key, firewall and size the preflight checks for."""
    provider.ssh_keys = {FINGERPRINT}
    provider.firewalls = {FIREWALL_ID: admitting_firewall(settings.owner_tag)}
    provider.sizes = [offered_size(settings, slug="s-1vcpu-1gb"), offered_size(settings)]


@dataclass
class Cloud:
    root: object
    settings: CloudSettings = field(default_factory=cloud_settings)
    provider: FakeProvider = field(default_factory=FakeProvider)
    channel: FakeChannel = field(default_factory=FakeChannel)
    clock: FakeClock = field(default_factory=FakeClock)
    transport: FakeTransport = field(default_factory=FakeTransport)

    def __post_init__(self):
        self.ledger = Ledger(self.root)
        # The fake provider stamps creation times and its action log from this clock.
        self.provider.clock = self.clock
        self.transport.clock = self.clock
        # Every DELETE the fake provider sees must already be recorded in this ledger.
        self.provider.ledger_root = self.ledger.root
        admit_preflight(self.provider, self.settings)
        operator = Path(tempfile.mkdtemp(dir=OPERATOR_FILES.name))
        self.key_path = operator / "access_key"
        self.key_path.write_text("stand-in for the access key; never used\n")
        self.bundle_path = operator / BUNDLE_NAME
        self.bundle_path.write_bytes(BUNDLE_BYTES)
        self.unrelated = [
            self.provider.seed(),
            self.provider.seed("web", "production"),
            self.provider.seed(FOREIGN_OWNER, run_tag("r1")),
            self.provider.seed(FOREIGN_OWNER),
            self.provider.seed(run_tag("r1")),
        ]

    def access_file(self):
        """The access settings as operations writes them."""
        return {"ssh_key_path": str(self.key_path), "ssh_key_fingerprint": FINGERPRINT,
                "firewall_id": FIREWALL_ID,
                "bundle": {"path": str(self.bundle_path), "name": BUNDLE_NAME,
                           "bytes": len(BUNDLE_BYTES), "sha256": BUNDLE_SHA256}}

    def wiring(self):
        return {"channel": self.channel, "clock": self.clock, "sleep": self.clock.advance,
                "access": AccessSettings.model_validate(self.access_file()),
                "transport": self.transport}

    def controller(self, **overrides):
        return Controller(self.ledger, self.provider, self.settings, **self.wiring() | overrides)

    def restarted(self):
        """A fresh controller over a fresh ledger object on the same directory."""
        return Controller(Ledger(self.root), self.provider, self.settings, **self.wiring())

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

    def launched(self, run_id="r1", controller=None):
        """Launch one run and tick until its game is ready and it is running."""
        controller = controller or self.controller()
        assert controller.launch(run_id)["outcome"] == "launched"
        for _ in range(5):
            controller.tick()
            if self.ledger.get(run_id).state != "provisioning":
                break
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
    """Answers the Droplet, action and preflight endpoints in memory, as an httpx handler."""

    def __init__(self, settings=None):
        settings = settings or cloud_settings()
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
        # A status here answers a DELETE with it; the Droplet is removed regardless, and
        # unless the status is 404 the action log shows its destroy.
        self.delete_status = None
        # A status here answers every read of a live Droplet by ID, as an outage would,
        # with these headers.
        self.read_status = None
        self.read_headers = {}
        # Stamps the destroy actions DELETEs leave in the action log, and the log.
        self.clock = FakeClock()
        # Each request and each wait of a client that sleeps through `wait`, in order, as
        # ("request", when, request) or ("wait", when, seconds).
        self.log = []
        self.keys = {FINGERPRINT}
        self.firewalls = {FIREWALL_ID: admitting_firewall(settings.owner_tag)}
        self.sizes = [offered_size(settings)]

    def wait(self, seconds):
        """A client's sleep before a retry, spent on the clock."""
        self.log.append(("wait", self.clock(), seconds))
        self.clock.advance(seconds)

    def __call__(self, request):
        self.requests.append(request)
        self.log.append(("request", self.clock(), request))
        path = request.url.path.removeprefix("/v2")
        if path == "/droplets" and request.method == "POST":
            return self.create(json.loads(request.content))
        if path == "/actions":
            return page_of("actions", self.actions)
        if path == "/sizes":
            return page_of("sizes", self.sizes)
        if path.startswith(("/account/keys/", "/firewalls/")):
            return self.preflight_read(path)
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
            return self.delete(droplet_id)
        if self.read_status is not None:
            return httpx.Response(self.read_status, headers=self.read_headers)
        return httpx.Response(200, json={"droplet": self.droplets[droplet_id]})

    def preflight_read(self, path):
        kind, name = path.rsplit("/", 1)
        if kind == "/account/keys" and name in self.keys:
            return httpx.Response(200, json={"ssh_key": {"id": 512, "fingerprint": name}})
        if kind == "/firewalls" and name in self.firewalls:
            return httpx.Response(200, json={"firewall": self.firewalls[name]})
        return httpx.Response(404, json={"id": "not_found", "message": "missing"})

    def create(self, body):
        if self.create_status is not None:
            return httpx.Response(self.create_status, json={"id": "server_error"})
        droplet_id = 5000 + len(self.requests)
        networks = {"v4": [{"ip_address": "192.0.2.200", "type": "private"},
                           {"ip_address": "198.51.100.7", "type": "public"}], "v6": []}
        self.droplets[droplet_id] = {"id": droplet_id, "name": body["name"], "status": "active",
                                     "tags": body["tags"], "created_at": "2026-11-24T12:00:00Z",
                                     "networks": networks}
        self.actions.append({"id": droplet_id + 1, "type": "create", "resource_type": "droplet",
                             "resource_id": droplet_id, "status": "completed",
                             "started_at": "2026-11-24T12:00:00Z"})
        return httpx.Response(202, json={"droplet": self.droplets[droplet_id]})

    def delete(self, droplet_id):
        del self.droplets[droplet_id]
        if self.delete_status != 404:
            self.actions.append({"id": 90000 + len(self.actions), "type": "destroy",
                                 "resource_type": "droplet", "resource_id": droplet_id,
                                 "status": "completed",
                                 "started_at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")})
        return httpx.Response(self.delete_status or 204)


def page_of(key, items):
    return httpx.Response(200, json={key: items, "links": {}, "meta": {"total": len(items)}})
