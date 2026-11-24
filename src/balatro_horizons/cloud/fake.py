"""In-memory provider and worker channel for offline tests; never a real Droplet."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from .provider import CreateAction, Droplet, ProviderError, WorkerReport


class SimulatedCrash(BaseException):
    """Stands in for the controller process dying; nothing in the controller catches it."""


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
    hidden: dict[int, int] = field(default_factory=dict)
    crash_at: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)

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
        self.crash("before_create")
        if self.fail_create:
            raise ProviderError(self.fail_create_code)
        created = self.clock() if self.clock else None
        droplet = Droplet(id=self.allocate(), name=spec.name, status="new", tags=spec.tags,
                          created_at=created)
        self.inventory[droplet.id] = droplet
        self.log_create(droplet.id)
        if self.listing_lag:
            self.hidden[droplet.id] = self.listing_lag
        self.crash("after_create")
        if self.partial_create:
            raise ProviderError("TRANSPORT_ERROR")
        return droplet

    def get_droplet(self, droplet_id):
        self.calls.append(("get", droplet_id))
        droplet = self.inventory.get(droplet_id)
        if droplet is not None and self.boot and droplet.status == "new":
            droplet = self.inventory[droplet_id] = replace(droplet, status="active")
        return droplet

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
        if self.action_failures:
            self.action_failures -= 1
            raise ProviderError("HTTP_503")
        return [action for action in self.actions if action.started_at >= since]

    def delete_droplet(self, droplet_id):
        self.calls.append(("delete", droplet_id))
        if self.delete_failures:
            self.delete_failures -= 1
            raise ProviderError("HTTP_500")
        self.crash("before_delete")
        found = droplet_id in self.inventory
        if not self.ghost_deletes:
            self.inventory.pop(droplet_id, None)
        self.crash("after_delete")
        return found

    def deleted(self):
        return [call[1] for call in self.calls if call[0] == "delete"]


@dataclass
class FakeChannel:
    reports: dict[str, WorkerReport] = field(default_factory=dict)

    def beat(self, run_id, at):
        self.reports[run_id] = WorkerReport(last_seen=at, finished=False)

    def finish(self, run_id, at):
        self.reports[run_id] = WorkerReport(last_seen=at, finished=True)

    def report(self, run_id):
        return self.reports.get(run_id, WorkerReport(last_seen=None, finished=False))
