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
    # When the provider made it; None when the provider did not say or said nonsense.
    created_at: datetime | None = None


class Provider(Protocol):
    def create_droplet(self, spec: DropletSpec) -> Droplet: ...

    def get_droplet(self, droplet_id: int) -> Droplet | None:
        """Return None when the provider answers 404: the Droplet is gone."""

    def list_droplets(self, tag: str) -> list[Droplet]: ...

    def find_droplets_by_name(self, name: str) -> list[Droplet]: ...

    def list_create_actions(self, since: datetime) -> list["CreateAction"]:
        """Every Droplet create action started at or after `since`, read completely.

        Creates that have no resource ID yet are kept, and so is each action's status.
        """

    def delete_droplet(self, droplet_id: int) -> None:
        """Delete one Droplet by ID; a 404 means it is already gone."""


@dataclass(frozen=True)
class CreateAction:
    # None while the provider has not yet named the Droplet it is making.
    resource_id: int | None
    started_at: datetime
    status: str


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
