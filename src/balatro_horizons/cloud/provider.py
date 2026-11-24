"""The provider and worker interfaces the controller depends on."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ProviderError(Exception):
    """A provider call that failed; the message is a short code, never a response body."""


@dataclass(frozen=True)
class DropletSpec:
    name: str
    region: str
    size: str
    image: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Droplet:
    id: int
    name: str
    status: str
    tags: tuple[str, ...]


class Provider(Protocol):
    def create_droplet(self, spec: DropletSpec) -> Droplet: ...

    def get_droplet(self, droplet_id: int) -> Droplet | None:
        """Return None when the provider answers 404: the Droplet is gone."""

    def list_droplets(self, tag: str) -> list[Droplet]: ...

    def delete_droplet(self, droplet_id: int) -> None:
        """Delete one Droplet by ID; a 404 means it is already gone."""


@dataclass(frozen=True)
class WorkerReport:
    last_seen: datetime | None
    finished: bool


class WorkerChannel(Protocol):
    """Heartbeat and completion reported by a worker; the transport is a later slice."""

    def report(self, run_id: str) -> WorkerReport: ...


class SilentChannel:
    """No worker payload exists yet, so a real worker never reports and is torn down."""

    def report(self, run_id):
        return WorkerReport(last_seen=None, finished=False)
