"""One JSON file per run, plus the unmatched watch and the last inventory read, written durably."""

import fcntl
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .durable import durable_json, ensure_directory

LIVE_STATES = (
    "provisioning", "create_unresolved", "running", "tearing_down", "cleanup_unverified",
)
RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
# The worker's stages, in the order it runs them.
Stage = Literal["packages", "dependencies", "bundle", "user", "display", "game", "ready",
                "play"]
# Text the worker reports is capped, so a run record stays small.
Bounded = Annotated[str, Field(max_length=4000)]


class LedgerBusy(RuntimeError):
    pass


class LedgerDamaged(ValueError):
    """A ledger path exists but is not what the ledger writes."""


class DestroyEvidence(BaseModel):
    """A destroy action the account action log showed for a resource."""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    resource_id: int
    status: Literal["in-progress", "completed", "errored"]
    started_at: AwareDatetime


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Firewalls and SSH keys join this list in a later slice.
    kind: Literal["droplet"] = "droplet"
    id: int
    # The latest DELETE attempt, written before the request is sent.
    delete_requested_at: AwareDatetime | None = None
    # When the provider acknowledged our DELETE.
    acknowledged_at: AwareDatetime | None = None
    # The destroy action found for this ID, and when the action log was last read for it
    # and how that read ended.
    destroy_evidence: DestroyEvidence | None = None
    destroy_checked_at: AwareDatetime | None = None
    destroy_check: Literal["found", "unknown", "failed", "timed_out"] | None = None


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: AwareDatetime
    state: str
    reason: str | None = None


class WorkerStatus(BaseModel):
    """The worker's status file: its stage, its committed moves, and on the way out, what
    it saw and how play ended."""

    model_config = ConfigDict(extra="forbid")

    stage: Stage
    # Only records kept from before play existed carry "ready".
    state: Literal["running", "finished", "failed", "ready"]
    reason: str | None = Field(default=None, max_length=200)
    actions: int = Field(default=0, ge=0)
    outcome: Literal["game_over", "won", "action_cap"] | None = None
    started_at: dict[Stage, AwareDatetime] = {}
    finished_at: AwareDatetime | None = None
    observed: dict[Literal["kernel", "packages_sha256", "steamrt4", "game_version",
                           "renderer", "cloud_init", "seed"], Bounded] = {}
    # Capped log tails, written only on failure.
    evidence: dict[Literal["stage_log", "display_stderr", "game_stderr"], Bounded] = {}


class RunRecord(BaseModel):
    # Times must carry a zone and amounts must be finite: the gate subtracts and sums them.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    run_id: str
    state: Literal["provisioning", "create_unresolved", "running", "tearing_down", "torn_down",
                   "cleanup_unverified"]
    created_at: AwareDatetime
    deadline: AwareDatetime
    reserved_usd: float = Field(ge=0)
    resources: list[Resource] = []
    # IDs whose tags stopped matching; the unmatched watch owns them from then on.
    released: list[int] = []
    create_attempted_at: AwareDatetime | None = None
    # A 4xx answered the create, so the provider made nothing.
    create_rejected: bool = False
    # The Droplet's public IPv4 address, for SSH only; status never shows it.
    address: str | None = None
    # The latest SSH attempt's failure, cleared by a success; it names a boot timeout.
    last_ssh_failure: Literal["unreachable", "host_key_mismatch"] | None = None
    worker_started_at: AwareDatetime | None = None
    # The worker's latest report, kept after the Droplet is gone.
    worker_status: WorkerStatus | None = None
    # The most committed moves a read has shown; only a higher count restarts the grace.
    actions_seen: int = Field(default=0, ge=0)
    grace_from: AwareDatetime | None = None
    # How the results copy went: "copied", or why not. Unset when none was tried.
    results_copy: str | None = None
    cancel_requested: bool = False
    teardown_reason: str | None = None
    teardown_started_at: AwareDatetime | None = None
    verified_at: AwareDatetime | None = None
    settled_usd: float | None = Field(default=None, ge=0)
    history: list[Transition] = []

    @property
    def live(self):
        return self.state in LIVE_STATES

    def resource_ids(self):
        return {resource.id for resource in self.resources}

    def resource(self, droplet_id):
        return next(resource for resource in self.resources if resource.id == droplet_id)

    def remember(self, droplet_id):
        if droplet_id not in self.resource_ids() and droplet_id not in self.released:
            self.resources.append(Resource(id=droplet_id))

    def release(self, droplet_id):
        self.resources = [r for r in self.resources if r.id != droplet_id]
        self.released.append(droplet_id)

    def move(self, state, at, reason=None):
        self.state = state
        self.history.append(Transition(at=at, state=state, reason=reason))


class UnmatchedDroplet(BaseModel):
    """An owner-tagged Droplet without one usable run tag: never deleted, only watched."""

    model_config = ConfigDict(extra="forbid")

    droplet_id: int
    found_at: AwareDatetime


class InventoryCheck(BaseModel):
    """The last read of the owner-tag inventory; an incomplete one closes the gate."""

    complete: bool
    at: AwareDatetime


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.unmatched_dir = self.root / "unmatched"
        self.inventory_path = self.root / "inventory.json"

    def get(self, run_id):
        path = self.runs_dir / f"{run_id}.json"
        return read_record(path) if present(path, "file") else None

    def runs(self):
        return [read_record(path) for path in json_files(self.runs_dir)]

    def save(self, record: RunRecord):
        durable_json(self.runs_dir / f"{record.run_id}.json", record.model_dump(mode="json"))

    def unmatched(self):
        return [UnmatchedDroplet.model_validate_json(path.read_text())
                for path in json_files(self.unmatched_dir)]

    def save_unmatched(self, record: UnmatchedDroplet):
        durable_json(self.unmatched_dir / f"{record.droplet_id}.json",
                     record.model_dump(mode="json"))

    def note_unmatched(self, droplet_id, now):
        """Start watching a Droplet this controller must not delete; it blocks from then on."""
        if any(record.droplet_id == droplet_id for record in self.unmatched()):
            return
        self.save_unmatched(UnmatchedDroplet(droplet_id=droplet_id, found_at=now))

    def inventory(self):
        if not present(self.inventory_path, "file"):
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


def present(path: Path, kind):
    """Whether a ledger path exists as a "file" or a "directory".

    Only a stat that says the path is not there counts as missing. A path of the wrong
    type is damage, and any other error (no permission, a file where a parent directory
    should be) is raised as it is: neither may read as an empty ledger.
    """
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    if not (stat.S_ISREG(mode) if kind == "file" else stat.S_ISDIR(mode)):
        raise LedgerDamaged("LEDGER_DAMAGED")
    return True


def json_files(directory: Path):
    if not present(directory, "directory"):
        return []
    # iterdir raises when the directory cannot be listed, where glob would hide it.
    # Interrupted writes leave only *.tmp files, which never count.
    return sorted(path for path in directory.iterdir() if path.name.endswith(".json"))


def read_record(path: Path):
    """Parse a run record, then check what every writer guarantees but the model cannot.

    A new record has no history until its first move(), so these are checked on records
    read back, not in the model: a record is always moved before it is saved, its state
    is only ever assigned by move(), and teardown settles it before moving it to torn_down.
    """
    record = RunRecord.model_validate_json(path.read_text())
    if not record.history or record.history[-1].state != record.state:
        raise LedgerDamaged("LEDGER_DAMAGED")
    if record.state == "torn_down" and record.settled_usd is None:
        raise LedgerDamaged("LEDGER_DAMAGED")
    return record


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
