"""Install and start the worker on an active Droplet, then read its status file.

The order is fixed: wait for SSH with the pinned host key, push the worker and its pins,
push the bundle, then start the worker detached, so no tick waits on the install. Each
later tick reads the worker's status file. The boot timeout covers all of it, from the
create until the worker reports the game ready.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .hostkey import known_hosts_path
from .ledger import WorkerStatus
from .ssh import RETRIED, Reply, ssh

# On the Droplet, root-only and outside any login home. The worker uses the same paths.
WORKER_DIR = "/var/lib/bh-worker"
STATUS_COMMAND = f"cat {WORKER_DIR}/status.json 2>/dev/null || true"
WORKER_SOURCE = Path(__file__).parent / "worker"
WORKER_FILES = ("worker.py", "pins.toml")
CALL_SECONDS = 60
STATUS_BYTES = 64 * 1024


@dataclass(frozen=True)
class Progress:
    """One boot step's result. With neither failure nor ready set, the run waits."""

    failure: str | None = None
    ready: bool = False
    # The worker's final report, kept on the run.
    status: WorkerStatus | None = None


@dataclass(frozen=True)
class StatusRead:
    reply: Reply
    status: WorkerStatus | None = None
    malformed: bool = False


def boot_timeout_reason(record):
    """A pin still unverified at the boot timeout is named; anything else is a boot failure."""
    return "host_key_mismatch" if record.last_ssh_failure == "host_key_mismatch" else "boot_failed"


class WorkerLink:
    """SSH calls to one run's Droplet, as root, through the run's pinned host key."""

    def __init__(self, transport, access, ledger_root):
        self.transport = transport
        self.access = access
        self.ledger_root = ledger_root

    def call(self, record, command, *, stdin=None, timeout=CALL_SECONDS):
        return ssh(self.transport, self.access.ssh_key_path,
                   known_hosts_path(self.ledger_root, record.run_id), record.address, command,
                   stdin=stdin, timeout=timeout)

    def read_status(self, record):
        reply = self.call(record, STATUS_COMMAND)
        if not reply.ok or not reply.stdout.strip():
            return StatusRead(reply)
        if len(reply.stdout) > STATUS_BYTES:
            return StatusRead(reply, malformed=True)
        try:
            return StatusRead(reply, WorkerStatus.model_validate_json(reply.stdout))
        except ValidationError:
            return StatusRead(reply, malformed=True)


class Install:
    """Boot steps for one provisioning run, from an active Droplet to a ready game."""

    def __init__(self, ledger, provider, link, settings):
        self.ledger = ledger
        self.provider = provider
        self.link = link
        self.settings = settings

    def step(self, record, now):
        if record.worker_started_at is not None:
            return self.check_worker(record)
        droplet, gone = self.read_droplet(record)
        if gone:
            return Progress(failure="boot_failed")
        if droplet is None or droplet.status != "active":
            return Progress()
        if droplet.public_ipv4 is None:
            return Progress(failure="no_public_ipv4")
        if record.address is None:
            record.address = droplet.public_ipv4
            self.ledger.save(record)
        return self.start_worker(record, now)

    def read_droplet(self, record):
        """The Droplet, and whether it is known gone; a failed read is neither."""
        droplet_id = min(record.resource_ids(), default=None)
        if droplet_id is None:
            return None, True
        try:
            droplet = self.provider.get_droplet(droplet_id)
        except Exception:
            return None, False
        return droplet, droplet is None

    def start_worker(self, record, now):
        for step, command, stdin, timeout in self.install_calls(record, now):
            reply = self.call(record, command, stdin=stdin, timeout=timeout)
            if not reply.ok:
                return failed_call(step, reply)
        record.worker_started_at = now
        self.ledger.save(record)
        return Progress()

    def install_calls(self, record, now):
        """The pinned probe, the worker files, the bundle, then the detached start."""
        bundle = self.link.access.bundle
        elapsed = (now - record.created_at).total_seconds()
        # The bundle push may take whatever is left of the boot timeout, and no more.
        push_seconds = max(self.settings.boot_timeout_seconds - elapsed, 1)
        yield "probe", "true", None, CALL_SECONDS
        for name in WORKER_FILES:
            source = (WORKER_SOURCE / name).read_bytes()
            yield "push_worker", push_command(name), source, CALL_SECONDS
        yield "push_bundle", push_command(bundle.name), bundle.path, push_seconds
        yield "start", start_command(bundle, record.deadline), None, CALL_SECONDS

    def check_worker(self, record):
        read = self.link.read_status(record)
        self.note_failure(record, read.reply)
        if not read.reply.ok:
            return failed_call("status", read.reply)
        if read.malformed:
            return Progress(failure="worker_status_malformed")
        status = read.status
        if status is None or status.state == "running":
            return Progress()
        if status.state == "failed":
            return Progress(failure=f"worker_failed:{status.stage}", status=status)
        return Progress(ready=True, status=status)

    def call(self, record, command, *, stdin, timeout):
        reply = self.link.call(record, command, stdin=stdin, timeout=timeout)
        self.note_failure(record, reply)
        return reply

    def note_failure(self, record, reply):
        """Keep the latest retried failure, cleared by a success, to name a boot timeout."""
        if not reply.ok and reply.outcome not in RETRIED:
            return
        failure = None if reply.ok else reply.outcome
        if record.last_ssh_failure != failure:
            record.last_ssh_failure = failure
            self.ledger.save(record)


def failed_call(step, reply):
    """Unreachable and mismatched hosts wait for the boot timeout; anything else is named."""
    if reply.outcome in RETRIED:
        return Progress()
    return Progress(failure=f"ssh_{reply.outcome}:{step}")


def push_command(name):
    """Write stdin to a root-only file, renamed into place so a retried push replaces it whole."""
    target = shlex.quote(f"{WORKER_DIR}/{name}")
    partial = shlex.quote(f"{WORKER_DIR}/{name}.part")
    return f"umask 077 && mkdir -p {WORKER_DIR} && cat > {partial} && mv {partial} {target}"


def start_command(bundle, deadline):
    log = f"{WORKER_DIR}/worker.log"
    return shlex.join([
        "systemd-run", "--unit=bh-worker", "--collect", "--property=UMask=0077",
        f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
        "/usr/bin/python3", "-I", f"{WORKER_DIR}/worker.py",
        "--bundle-name", bundle.name, "--bundle-bytes", str(bundle.bytes),
        "--bundle-sha256", bundle.sha256, "--deadline", deadline.isoformat(),
    ])
