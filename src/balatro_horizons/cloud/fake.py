"""In-memory provider and worker channel for offline tests; never a real Droplet."""

from dataclasses import dataclass, field, replace

from .provider import Droplet, ProviderError, WorkerReport


class SimulatedCrash(BaseException):
    """Stands in for the controller process dying; nothing in the controller catches it."""


@dataclass
class FakeProvider:
    inventory: dict[int, Droplet] = field(default_factory=dict)
    next_id: int = 1000
    fail_create: bool = False
    # Create the Droplet, then fail the call, as a timeout after acceptance would.
    partial_create: bool = False
    boot: bool = True
    delete_failures: int = 0
    # Delete answers success but the Droplet stays, so only verification catches it.
    ghost_deletes: bool = False
    list_failures: int = 0
    crash_at: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)

    def seed(self, *tags, status="active"):
        droplet = Droplet(id=self.allocate(), name="seeded", status=status, tags=tuple(tags))
        self.inventory[droplet.id] = droplet
        return droplet

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
            raise ProviderError("HTTP_422")
        droplet = Droplet(id=self.allocate(), name=spec.name, status="new", tags=spec.tags)
        self.inventory[droplet.id] = droplet
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
        if self.list_failures:
            self.list_failures -= 1
            raise ProviderError("HTTP_503")
        return [d for d in self.inventory.values() if tag in d.tags]

    def delete_droplet(self, droplet_id):
        self.calls.append(("delete", droplet_id))
        if self.delete_failures:
            self.delete_failures -= 1
            raise ProviderError("HTTP_500")
        self.crash("before_delete")
        if not self.ghost_deletes:
            self.inventory.pop(droplet_id, None)
        self.crash("after_delete")

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
