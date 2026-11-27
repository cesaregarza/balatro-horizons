"""The provider and worker interfaces the controller depends on."""

from collections.abc import Callable
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
    # The registered key's fingerprint, as the provider records it.
    ssh_key: str
    # The cloud-config that installs the run's pinned host key.
    user_data: str


@dataclass(frozen=True)
class Droplet:
    id: int
    name: str
    status: str
    tags: tuple[str, ...]
    # When the provider made it; None when the provider did not say or said nonsense.
    created_at: datetime | None = None
    # None until the provider reports a usable public IPv4 address.
    public_ipv4: str | None = None


@dataclass(frozen=True)
class InboundRule:
    protocol: str
    ports: str
    # Each source as (kind, value), for example ("addresses", "203.0.113.7/32").
    sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Firewall:
    tags: tuple[str, ...]
    inbound_rules: tuple[InboundRule, ...]


@dataclass(frozen=True)
class Size:
    slug: str
    available: bool
    price_hourly: float
    regions: tuple[str, ...]


class Provider(Protocol):
    def create_droplet(self, spec: DropletSpec) -> Droplet: ...

    def get_droplet(self, droplet_id: int, *,
                    time_left: Callable[[], float] | None = None) -> Droplet | None:
        """Return None when the provider answers 404: the Droplet is gone.

        `time_left`, when given, returns the seconds the read may still take. No attempt
        starts once none is left, each attempt and each wait before a retry fits in what
        is left, and a read that runs out raises. Without it, the read keeps its full
        retry policy.
        """

    def list_droplets(self, tag: str) -> list[Droplet]: ...

    def find_droplets_by_name(self, name: str) -> list[Droplet]: ...

    def list_create_actions(self, since: datetime) -> list["CreateAction"]:
        """Every Droplet create action started at or after `since`, read completely.

        Creates that have no resource ID yet are kept, and so is each action's status.
        """

    def list_destroy_actions(self, *,
                             time_left: Callable[[], float] | None = None) -> list["DestroyAction"]:
        """Every Droplet destroy action in the account action log, read completely.

        `time_left`, when given, budgets each page's attempts and waits: they fit in what
        is left, and a read that runs out raises NO_TIME_LEFT. Decoding a received page is
        not budgeted, so the caller rechecks the budget before trusting the result. Without
        `time_left`, every page keeps the full retry policy.
        """

    def delete_droplet(self, droplet_id: int) -> bool:
        """Delete one Droplet by ID; True only when the provider acknowledged the DELETE."""

    def ssh_key_registered(self, fingerprint: str) -> bool: ...

    def get_firewall(self, firewall_id: str) -> Firewall | None:
        """Return None when the provider answers 404."""

    def list_sizes(self) -> list[Size]: ...


@dataclass(frozen=True)
class CreateAction:
    # None while the provider has not yet named the Droplet it is making.
    resource_id: int | None
    started_at: datetime
    status: str


@dataclass(frozen=True)
class DestroyAction:
    id: int
    resource_id: int | None
    started_at: datetime
    status: str


@dataclass(frozen=True)
class WorkerReport:
    last_seen: datetime | None
    finished: bool


class WorkerChannel(Protocol):
    """Heartbeat and completion reported by a running worker."""

    def report(self, run_id: str) -> WorkerReport: ...
