"""One JSON file per run, written durably, plus append-only alert records."""

import fcntl
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .durable import durable_json, ensure_directory

LIVE_STATES = (
    "provisioning", "create_unresolved", "running", "tearing_down", "cleanup_unverified",
)
RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"


class LedgerBusy(RuntimeError):
    pass


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Firewalls and SSH keys join this list in a later slice.
    kind: Literal["droplet"] = "droplet"
    id: int


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    state: str
    reason: str | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: Literal["provisioning", "create_unresolved", "running", "tearing_down", "torn_down",
                   "cleanup_unverified"]
    created_at: datetime
    deadline: datetime
    reserved_usd: float
    resources: list[Resource] = []
    # IDs whose tags stopped matching; the unmatched watch owns them from then on.
    released: list[int] = []
    create_attempted_at: datetime | None = None
    grace_from: datetime | None = None
    cancel_requested: bool = False
    teardown_reason: str | None = None
    teardown_started_at: datetime | None = None
    verified_at: datetime | None = None
    settled_usd: float | None = None
    history: list[Transition] = []

    @property
    def live(self):
        return self.state in LIVE_STATES

    def resource_ids(self):
        return {resource.id for resource in self.resources}

    def remember(self, droplet_id):
        if droplet_id not in self.resource_ids() and droplet_id not in self.released:
            self.resources.append(Resource(id=droplet_id))

    def release(self, droplet_id):
        self.resources = [r for r in self.resources if r.id != droplet_id]
        self.released.append(droplet_id)

    def move(self, state, at, reason=None):
        self.state = state
        self.history.append(Transition(at=at, state=state, reason=reason))


class Alert(BaseModel):
    code: str
    at: datetime
    run_id: str | None = None
    droplet_id: int | None = None


class UnmatchedDroplet(BaseModel):
    """An owner-tagged Droplet without one usable run tag: never deleted, only watched."""

    model_config = ConfigDict(extra="forbid")

    droplet_id: int
    found_at: datetime
    gone_at: datetime | None = None


class InventoryCheck(BaseModel):
    """The last read of the owner-tag inventory; an incomplete one closes the gate."""

    complete: bool
    at: datetime


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.alerts_dir = self.root / "alerts"
        self.unmatched_dir = self.root / "unmatched"
        self.inventory_path = self.root / "inventory.json"

    def get(self, run_id):
        path = self.runs_dir / f"{run_id}.json"
        return RunRecord.model_validate_json(path.read_text()) if path.is_file() else None

    def runs(self):
        return [RunRecord.model_validate_json(path.read_text()) for path in json_files(self.runs_dir)]

    def save(self, record: RunRecord):
        durable_json(self.runs_dir / f"{record.run_id}.json", record.model_dump(mode="json"))

    def record_alert(self, code, at: datetime, *, run_id=None, droplet_id=None):
        alert = Alert(code=code, at=at, run_id=run_id, droplet_id=droplet_id)
        subject = run_id if run_id is not None else f"droplet-{droplet_id}"
        path = self.alerts_dir / f"{at.strftime('%Y%m%dT%H%M%S%fZ')}-{subject}-{code}.json"
        durable_json(path, alert.model_dump(mode="json"))

    def alerts(self):
        return [Alert.model_validate_json(path.read_text()) for path in json_files(self.alerts_dir)]

    def unmatched(self):
        return [UnmatchedDroplet.model_validate_json(path.read_text())
                for path in json_files(self.unmatched_dir)]

    def save_unmatched(self, record: UnmatchedDroplet):
        durable_json(self.unmatched_dir / f"{record.droplet_id}.json",
                     record.model_dump(mode="json"))

    def note_unmatched(self, droplet_id, now):
        """Start watching a Droplet this controller must not delete; alert once."""
        current = next((r for r in self.unmatched() if r.droplet_id == droplet_id), None)
        if current is not None and current.gone_at is None:
            return
        self.save_unmatched(UnmatchedDroplet(droplet_id=droplet_id, found_at=now))
        self.record_alert("UNMATCHED_DROPLET", now, droplet_id=droplet_id)

    def inventory(self):
        if not self.inventory_path.is_file():
            return None
        return InventoryCheck.model_validate_json(self.inventory_path.read_text())

    def save_inventory(self, complete, at):
        check = InventoryCheck(complete=complete, at=at)
        durable_json(self.inventory_path, check.model_dump(mode="json"))

    @contextmanager
    def lock(self, wait_seconds=0.0):
        """Serialize every ledger mutation between the controller and the reconciler."""
        with exclusive(self.root / "ledger.lock", wait_seconds, "LEDGER_BUSY"):
            yield

    @contextmanager
    def controller_lock(self):
        """Held for a controller's lifetime, so a restart knows the previous one is gone."""
        with exclusive(self.root / "controller.lock", 0.0, "CONTROLLER_BUSY"):
            yield


def json_files(directory: Path):
    # Interrupted writes leave only *.tmp files, which never match this pattern.
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


@contextmanager
def exclusive(path: Path, wait_seconds, busy_code):
    ensure_directory(path.parent)
    give_up = time.monotonic() + wait_seconds
    with path.open("a") as stream:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= give_up:
                    raise LedgerBusy(busy_code) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
