"""The run lifecycle: persist, create, supervise, and always tear down.

Every operation reads the ledger from disk under the ledger lock, so the ledger is
the only state and a reconciler can interleave between ticks without racing.
"""

import re
import time
from datetime import timedelta

from .channel import SshChannel
from .creation import CreateResolver, definite_rejection
from .gate import launch_refusals
from .hostkey import (
    HostKeyError,
    generate_host_key,
    known_hosts_path,
    pin_host_key,
    user_data,
)
from .install import CALL_SECONDS, Install, WorkerLink, stop_reason
from .ledger import RUN_ID_PATTERN, RunRecord
from .preflight import preflight_refusals
from .provider import DropletSpec
from .reconcile import reconcile_inventory
from .results import copy_results
from .settings import droplet_name, run_tag, utc_now
from .teardown import Teardown

SUPERVISED_STATES = ("provisioning", "running")
# The reconciler holds the ledger lock while it verifies cleanup, up to the cleanup
# bound for each run it tears down, so the controller waits rather than failing.
LOCK_WAIT_SECONDS = 1800


def require_run_id(run_id):
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError("INVALID_RUN_ID")
    return run_id


class Controller:
    """Launching and booting need the access settings and an SSH transport; teardown never does."""

    def __init__(self, ledger, provider, settings, *, access=None, transport=None,
                 channel=None, clock=utc_now, sleep=time.sleep):
        self.ledger = ledger
        self.provider = provider
        self.settings = settings
        self.access = access
        self.transport = transport
        self.clock = clock
        self.sleep = sleep
        self.teardown = Teardown(ledger, provider, settings, clock, sleep)
        self.resolver = CreateResolver(ledger, provider, settings, self.teardown, clock)
        self.link = WorkerLink(transport, access, ledger.root)
        self.install = Install(ledger, provider, self.link, settings, clock)
        self.channel = channel or SshChannel(ledger, self.link)

    def launch(self, run_id):
        require_run_id(run_id)
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            existing = self.ledger.get(run_id)
            if existing is not None:
                return {"outcome": "duplicate", "run_id": run_id, "state": existing.state}
            reasons = self.admission_refusals()
            if reasons:
                return {"outcome": "refused", "run_id": run_id, "reasons": reasons}
            return self.create(run_id)

    def admission_refusals(self):
        # Admit nothing until what we already own is accounted for.
        reconcile_inventory(self.ledger, self.provider, self.settings, self.teardown,
                            self.clock)
        # The preflight only reads, and runs before the run is recorded, so its refusal
        # leaves no record. It runs only once the gate admits the launch.
        return (launch_refusals(self.ledger, self.settings, self.clock())
                or preflight_refusals(self.provider, self.settings, self.access))

    def create(self, run_id):
        """Pin a fresh host key, persist the run, then create its Droplet."""
        try:
            host_key = generate_host_key(self.transport)
            pin_host_key(known_hosts_path(self.ledger.root, run_id), host_key)
        except (HostKeyError, OSError):
            # Nothing is recorded or created yet, so this refuses like the preflight.
            return {"outcome": "refused", "run_id": run_id, "reasons": ["HOST_KEY_UNAVAILABLE"]}
        record = self.persist_new(run_id)
        try:
            droplet = self.provider.create_droplet(self.spec(run_id, host_key))
        except Exception as error:
            return self.create_failed(record, error)
        record.remember(droplet.id)
        self.ledger.save(record)
        return {"outcome": "launched", "run_id": run_id, "state": record.state}

    def create_failed(self, record, error):
        if definite_rejection(error):
            record.create_rejected = True
            record = self.resolver.tear_down(record, "create_failed")
        else:
            # The create may have succeeded; only positive evidence may release it.
            record = self.resolver.hold(record, "create_failed")
        outcome = "create_unresolved" if record.state == "create_unresolved" else "create_failed"
        return {"outcome": outcome, "run_id": record.run_id, "state": record.state}

    def persist_new(self, run_id):
        """Write the run and its absolute deadline before the create."""
        now = self.clock()
        record = RunRecord(
            run_id=run_id, state="provisioning", created_at=now,
            deadline=now + timedelta(hours=self.settings.max_run_hours),
            reserved_usd=self.settings.worst_case_usd, create_attempted_at=now,
        )
        record.move("provisioning", now)
        self.ledger.save(record)
        return record

    def spec(self, run_id, host_key):
        return DropletSpec(
            name=droplet_name(run_id), region=self.settings.region, size=self.settings.size,
            image=self.settings.image, tags=(self.settings.owner_tag, run_tag(run_id)),
            ssh_key=self.access.ssh_key_fingerprint, user_data=user_data(host_key),
        )

    def tick(self):
        """Evaluate every live run once; True while any run still needs supervision."""
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            for record in self.ledger.runs():
                if record.live:
                    self.evaluate(record)
            return any(record.state in SUPERVISED_STATES for record in self.ledger.runs())

    def evaluate(self, record):
        if record.state in ("create_unresolved", "tearing_down", "cleanup_unverified"):
            return self.resolver.tear_down(record, record.teardown_reason)
        if record.state == "provisioning":
            return self.check_boot(record)
        reason = self.teardown_reason(record)
        return self.resolver.tear_down(record, reason) if reason else record

    def teardown_reason(self, record):
        """Why a running run ends in this tick, if it does. Only a higher committed-move
        count, read by this controller, restarts the heartbeat grace."""
        now = self.clock()
        reason = stop_reason(record, self.settings, now)
        if reason:
            return reason
        left = (record.deadline - now).total_seconds()
        report = self.channel.report(record.run_id, timeout=min(CALL_SECONDS, left))
        if report.status is not None:
            # Saved with this tick's teardown or progress, as evidence even when a stop
            # reason wins; a read that brings neither changes nothing worth keeping.
            record.worker_status = report.status
        # The read took time, so its report counts only if no stop reason holds once it has
        # returned, and the heartbeat grace is judged at that time too.
        read_at = self.clock()
        reason = stop_reason(record, self.settings, read_at)
        if reason:
            return reason
        if report.failure:
            return report.failure
        if report.finished:
            record.results_copy = copy_results(self.link, record, self.ledger.root,
                                               self.clock)
            return "succeeded"
        if report.actions is not None and report.actions > record.actions_seen:
            record.actions_seen = report.actions
            record.grace_from = read_at
            self.ledger.save(record)
        if read_at - record.grace_from > timedelta(seconds=self.settings.heartbeat_grace_seconds):
            return "heartbeat_lost"
        return None

    def check_boot(self, record):
        """A run provisions from the create until its worker reports that play has started."""
        reason = stop_reason(record, self.settings, self.clock())
        if reason:
            return self.resolver.tear_down(record, reason)
        progress = self.install.step(record)
        if progress.status is not None:
            record.worker_status = progress.status
        if progress.failure:
            return self.resolver.tear_down(record, progress.failure)
        # The step took time, so a stop reason that holds once it has returned comes first:
        # play is then too late, and no news is not waited on.
        returned_at = self.clock()
        reason = stop_reason(record, self.settings, returned_at)
        if reason:
            return self.resolver.tear_down(record, reason)
        if progress.playing:
            record.actions_seen = progress.status.actions
            record.grace_from = returned_at
            record.move("running", returned_at)
            self.ledger.save(record)
        return record

    def cancel(self, run_id, *, now=False):
        """Record the request; with now=True also tear down in this call."""
        require_run_id(run_id)
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            record = self.ledger.get(run_id)
            if record is None:
                raise ValueError("UNKNOWN_RUN")
            if record.live and not record.cancel_requested:
                record.cancel_requested = True
                self.ledger.save(record)
            if record.live and now:
                record = self.evaluate(record)
            return {"run_id": run_id, "state": record.state, "cancel_requested": True}

    def recover(self):
        """Re-evaluate every live run after a controller restart."""
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            for record in self.ledger.runs():
                if not record.live:
                    continue
                if record.state == "provisioning":
                    self.abandon_provisioning(record)
                    continue
                if record.state == "running":
                    record.grace_from = self.clock()
                    self.ledger.save(record)
                self.evaluate(record)
            reconcile_inventory(self.ledger, self.provider, self.settings, self.teardown,
                                self.clock)

    def abandon_provisioning(self, record):
        """Never adopt a half-made worker: its boot was not observed."""
        return self.resolver.tear_down(record, "restart_while_provisioning")

    def supervise(self):
        """Tick until no run needs supervision; any error tears live runs down first."""
        try:
            while self.tick():
                self.sleep(self.settings.poll_seconds)
        except BaseException:
            self.abandon()
            raise

    def abandon(self):
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            for record in self.ledger.runs():
                if record.state in SUPERVISED_STATES:
                    self.resolver.tear_down(record, "controller_error")
