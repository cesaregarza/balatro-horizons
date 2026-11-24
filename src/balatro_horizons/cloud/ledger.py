"""One JSON file per run, replaced atomically, plus append-only alert records."""

import fcntl
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from balatro_horizons.storage.journal import atomic_json

LIVE_STATES = ("provisioning", "running", "tearing_down", "cleanup_unverified")
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
    state: Literal["provisioning", "running", "tearing_down", "torn_down", "cleanup_unverified"]
    created_at: datetime
    deadline: datetime
    reserved_usd: float
    resources: list[Resource] = []
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
        if droplet_id not in self.resource_ids():
            self.resources.append(Resource(id=droplet_id))

    def move(self, state, at, reason=None):
        self.state = state
        self.history.append(Transition(at=at, state=state, reason=reason))


class RunAlert(BaseModel):
    run_id: str
    code: str
    at: datetime


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.alerts_dir = self.root / "alerts"

    def get(self, run_id):
        path = self.runs_dir / f"{run_id}.json"
        return RunRecord.model_validate_json(path.read_text()) if path.is_file() else None

    def runs(self):
        # Interrupted writes leave only *.tmp files, which never match this pattern.
        paths = sorted(self.runs_dir.glob("*.json")) if self.runs_dir.is_dir() else []
        return [RunRecord.model_validate_json(path.read_text()) for path in paths]

    def save(self, record: RunRecord):
        atomic_json(self.runs_dir / f"{record.run_id}.json", record.model_dump(mode="json"))

    def record_alert(self, run_id, code, at: datetime):
        stamp = at.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.alerts_dir / f"{stamp}-{run_id}-{code}.json"
        atomic_json(path, {"run_id": run_id, "code": code, "at": at.isoformat()})

    def alerts(self):
        if not self.alerts_dir.is_dir():
            return []
        return [RunAlert.model_validate_json(path.read_text())
                for path in sorted(self.alerts_dir.glob("*.json"))]

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


@contextmanager
def exclusive(path: Path, wait_seconds, busy_code):
    path.parent.mkdir(parents=True, exist_ok=True)
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
