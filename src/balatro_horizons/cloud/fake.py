"""In-memory provider and worker channel for offline tests; never a real Droplet."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .digitalocean import parse_firewall, parse_size, parse_time
from .provider import CreateAction, DestroyAction, Droplet, ProviderError, WorkerReport


class SimulatedCrash(BaseException):
    """Stands in for the controller process dying; nothing in the controller catches it."""


class DeleteNotRecorded(BaseException):
    """A DELETE was sent before the ledger on disk held the attempt; teardown cannot catch it."""


@dataclass
class FakeProvider:
    inventory: dict[int, Droplet] = field(default_factory=dict)
    next_id: int = 1000
    fail_create: bool = False
    # A 4xx is a definite rejection; a 5xx or transport error leaves the create ambiguous.
    fail_create_code: str = "HTTP_422"
    # Create the Droplet, then fail the call, as a timeout after acceptance would.
    partial_create: bool = False
    boot: bool = True
    delete_failures: int = 0
    # Delete answers success but the Droplet stays, so only verification catches it.
    ghost_deletes: bool = False
    list_failures: int = 0
    action_failures: int = 0
    # New Droplets stay out of listings for this many listing calls, as a lagging index would.
    listing_lag: int = 0
    # New creates show in the action log as in progress, with no resource ID yet.
    pending_creates: bool = False
    clock: Callable[[], datetime] | None = None
    actions: list[CreateAction] = field(default_factory=list)
    destroys: list[DestroyAction] = field(default_factory=list)
    # Our DELETEs show in the action log as completed destroys.
    log_destroys: bool = True
    # After a DELETE it accepts, the Droplet reads back by ID this many times, with no tags
    # and in no tag listing, before it reads 404, as one being destroyed would. Its destroy
    # shows in progress until then.
    destroy_reads: int = 0
    # Accept a DELETE, starting its destroy, then fail the call, as a timeout after
    # acceptance would.
    partial_delete: bool = False
    # The Droplets being destroyed, each with the reads by ID it has left.
    destroying: dict[int, int] = field(default_factory=dict)
    # A destroy read takes this many pages, each this many seconds on the clock, which must
    # then be one that advances.
    destroy_pages: int = 1
    destroy_page_seconds: float = 0
    # Created Droplets get no public IPv4 address.
    no_public_ipv4: bool = False
    # The preflight's reads: registered fingerprints, then firewalls and sizes as the API
    # returns them.
    ssh_keys: set[str] = field(default_factory=set)
    firewalls: dict[str, dict] = field(default_factory=dict)
    sizes: list[dict] = field(default_factory=list)
    preflight_failures: int = 0
    # With a ledger directory, every DELETE must already be recorded there.
    ledger_root: Path | None = None
    hidden: dict[int, int] = field(default_factory=dict)
    crash_at: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)
    specs: list = field(default_factory=list)

    def seed(self, *tags, status="active", created_at=None):
        droplet = Droplet(id=self.allocate(), name="seeded", status=status, tags=tuple(tags),
                          created_at=created_at)
        self.inventory[droplet.id] = droplet
        self.log_create(droplet.id, created_at)
        return droplet

    def log_create(self, droplet_id, at=None):
        started = at or (self.clock() if self.clock else None)
        if started is None:
            return
        if self.pending_creates:
            action = CreateAction(resource_id=None, started_at=started, status="in-progress")
        else:
            action = CreateAction(resource_id=droplet_id, started_at=started, status="completed")
        self.actions.append(action)

    def allocate(self):
        self.next_id += 1
        return self.next_id

    def crash(self, point):
        if point in self.crash_at:
            self.crash_at.discard(point)
            raise SimulatedCrash(point)

    def create_droplet(self, spec):
        self.calls.append(("create", spec.name, spec.tags))
        self.specs.append(spec)
        self.crash("before_create")
        if self.fail_create:
            raise ProviderError(self.fail_create_code)
        created = self.clock() if self.clock else None
        droplet_id = self.allocate()
        address = None if self.no_public_ipv4 else f"192.0.2.{droplet_id % 254 + 1}"
        droplet = Droplet(id=droplet_id, name=spec.name, status="new", tags=spec.tags,
                          created_at=created, public_ipv4=address)
        self.inventory[droplet.id] = droplet
        self.log_create(droplet.id)
        if self.listing_lag:
            self.hidden[droplet.id] = self.listing_lag
        self.crash("after_create")
        if self.partial_create:
            raise ProviderError("TRANSPORT_ERROR")
        return droplet

    def get_droplet(self, droplet_id, *, time_left=None):
        # It answers at once, so a limit on the time the read may take never applies.
        self.calls.append(("get", droplet_id))
        droplet = self.inventory.get(droplet_id)
        if droplet is not None and droplet_id in self.destroying:
            self.read_while_destroyed(droplet_id)
        elif droplet is not None and self.boot and droplet.status == "new":
            droplet = self.inventory[droplet_id] = replace(droplet, status="active")
        return droplet

    def read_while_destroyed(self, droplet_id):
        """Count one read of a Droplet being destroyed. After its last, it is gone and its
        destroy completed."""
        self.destroying[droplet_id] -= 1
        if self.destroying[droplet_id] == 0:
            del self.destroying[droplet_id], self.inventory[droplet_id]
            self.destroys = [replace(action, status="completed")
                             if (action.resource_id, action.status) == (droplet_id, "in-progress")
                             else action for action in self.destroys]

    def list_droplets(self, tag):
        self.calls.append(("list", tag))
        return self.visible(lambda d: tag in d.tags)

    def find_droplets_by_name(self, name):
        self.calls.append(("name", name))
        return self.visible(lambda d: d.name.lower() == name.lower())

    def visible(self, matches):
        if self.list_failures:
            self.list_failures -= 1
            raise ProviderError("HTTP_503")
        found = [d for d in self.inventory.values()
                 if matches(d) and not self.hidden.get(d.id)]
        self.hidden = {i: max(n - 1, 0) for i, n in self.hidden.items()}
        return found

    def list_create_actions(self, since):
        self.calls.append(("actions", since))
        self.read_actions()
        return [action for action in self.actions if action.started_at >= since]

    def list_destroy_actions(self, since=None, *, time_left=None):
        self.calls.append(("destroys", since))
        self.read_actions()
        for _ in range(self.destroy_pages):
            # As in the client, with `time_left` no page starts once no time is left, and a
            # page that needs more than is left runs until none is, then raises.
            left = None if time_left is None else time_left()
            if left is not None and (left <= 0 or self.destroy_page_seconds > left):
                self.spend(max(left, 0))
                raise ProviderError("NO_TIME_LEFT")
            self.spend(self.destroy_page_seconds)
        return [action for action in self.destroys if since is None or action.started_at >= since]

    def spend(self, seconds):
        if seconds:
            self.clock.advance(seconds)

    def read_actions(self):
        if self.action_failures:
            self.action_failures -= 1
            raise ProviderError("HTTP_503")

    def log_destroy(self, droplet_id, status="completed", at=None):
        started = at or self.clock()
        self.destroys.append(DestroyAction(id=len(self.destroys) + 1, resource_id=droplet_id,
                                           started_at=started, status=status))

    def delete_droplet(self, droplet_id):
        self.calls.append(("delete", droplet_id))
        self.require_recorded_delete(droplet_id)
        if self.delete_failures:
            self.delete_failures -= 1
            raise ProviderError("HTTP_500")
        self.crash("before_delete")
        # A Droplet already being destroyed answers as one that is gone.
        found = droplet_id in self.inventory and droplet_id not in self.destroying
        if found and not self.ghost_deletes:
            self.destroy(droplet_id)
        elif found and self.log_destroys and self.clock:
            self.log_destroy(droplet_id)
        self.crash("after_delete")
        if found and self.partial_delete:
            raise ProviderError("TRANSPORT_ERROR")
        return found

    def destroy(self, droplet_id):
        """Remove the Droplet at once, or with `destroy_reads`, strip its tags and remove it
        after that many reads. Its destroy is logged, in progress until it is gone."""
        logged = self.log_destroys and self.clock is not None
        if not self.destroy_reads:
            del self.inventory[droplet_id]
            if logged:
                self.log_destroy(droplet_id)
            return
        self.inventory[droplet_id] = replace(self.inventory[droplet_id], tags=())
        self.destroying[droplet_id] = self.destroy_reads
        if logged:
            self.log_destroy(droplet_id, status="in-progress")

    def require_recorded_delete(self, droplet_id):
        """The run's file on disk must already hold this attempt, stamped now."""
        if self.ledger_root is None or self.clock is None:
            return
        stamps = [parse_time(resource.get("delete_requested_at"))
                  for path in sorted((self.ledger_root / "runs").glob("*.json"))
                  for resource in json.loads(path.read_text())["resources"]
                  if resource["id"] == droplet_id]
        if self.clock() not in stamps:
            raise DeleteNotRecorded(droplet_id)

    def ssh_key_registered(self, fingerprint):
        self.calls.append(("ssh_key", fingerprint))
        self.read_preflight()
        return fingerprint in self.ssh_keys

    def get_firewall(self, firewall_id):
        self.calls.append(("firewall", firewall_id))
        self.read_preflight()
        found = self.firewalls.get(firewall_id)
        return None if found is None else parse_firewall(found)

    def list_sizes(self):
        self.calls.append(("sizes",))
        self.read_preflight()
        return [parse_size(size) for size in self.sizes]

    def read_preflight(self):
        if self.preflight_failures:
            self.preflight_failures -= 1
            raise ProviderError("HTTP_503")

    def deleted(self):
        return [call[1] for call in self.calls if call[0] == "delete"]


@dataclass
class FakeChannel:
    """Each run's latest report stands until the next, as a status file does."""

    reports: dict[str, WorkerReport] = field(default_factory=dict)

    def progress(self, run_id):
        """The worker commits one more move."""
        self.reports[run_id] = WorkerReport(actions=self.actions(run_id) + 1)

    def finish(self, run_id):
        self.reports[run_id] = WorkerReport(actions=self.actions(run_id), finished=True)

    def actions(self, run_id):
        return self.reports.get(run_id, WorkerReport()).actions or 0

    def report(self, run_id, timeout):
        return self.reports.get(run_id, WorkerReport())
