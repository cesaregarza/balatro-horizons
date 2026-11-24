"""The run lifecycle: persist, create, supervise, and always tear down.

Every operation reads the ledger from disk under the ledger lock, so the ledger is
the only state and a reconciler can interleave between ticks without racing.
"""

import re
import time
from datetime import timedelta

from .creation import CreateResolver, definite_rejection
from .gate import launch_refusals
from .ledger import RUN_ID_PATTERN, RunRecord
from .provider import DropletSpec, SilentChannel
from .reconcile import reconcile_inventory
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
    def __init__(self, ledger, provider, settings, *, channel=None, clock=utc_now,
                 sleep=time.sleep):
        self.ledger = ledger
        self.provider = provider
        self.settings = settings
        self.channel = channel or SilentChannel()
        self.clock = clock
        self.sleep = sleep
        self.teardown = Teardown(ledger, provider, settings, clock, sleep)
        self.resolver = CreateResolver(ledger, provider, settings, self.teardown, clock)

    def launch(self, run_id):
        require_run_id(run_id)
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            existing = self.ledger.get(run_id)
            if existing is not None:
                return {"outcome": "duplicate", "run_id": run_id, "state": existing.state}
            # Admit nothing until what we already own is accounted for.
            reconcile_inventory(self.ledger, self.provider, self.settings, self.teardown,
                                self.clock)
            reasons = launch_refusals(self.ledger, self.settings, self.clock())
            if reasons:
                return {"outcome": "refused", "run_id": run_id, "reasons": reasons}
            record = self.persist_new(run_id)
            try:
                droplet = self.provider.create_droplet(self.spec(run_id))
            except Exception as error:
                return self.create_failed(record, error)
            record.remember(droplet.id)
            self.ledger.save(record)
            return {"outcome": "launched", "run_id": run_id, "state": record.state}

    def create_failed(self, record, error):
        if definite_rejection(error):
            record = self.teardown.run(record, "create_failed")
        else:
            # The create may have succeeded; only positive evidence may release it.
            record = self.resolver.hold(record, "create_failed")
        outcome = "create_unresolved" if record.state == "create_unresolved" else "create_failed"
        return {"outcome": outcome, "run_id": record.run_id, "state": record.state}

    def persist_new(self, run_id):
        """Write the run and its absolute deadline before any provider call."""
        now = self.clock()
        record = RunRecord(
            run_id=run_id, state="provisioning", created_at=now,
            deadline=now + timedelta(hours=self.settings.max_run_hours),
            reserved_usd=self.settings.worst_case_usd, create_attempted_at=now,
        )
        record.move("provisioning", now)
        self.ledger.save(record)
        return record

    def spec(self, run_id):
        return DropletSpec(
            name=droplet_name(run_id), region=self.settings.region, size=self.settings.size,
            image=self.settings.image, tags=(self.settings.owner_tag, run_tag(run_id)),
        )

    def tick(self):
        """Evaluate every live run once; True while any run still needs supervision."""
        with self.ledger.lock(LOCK_WAIT_SECONDS):
            for record in self.ledger.runs():
                if record.live:
                    self.evaluate(record)
            return any(record.state in SUPERVISED_STATES for record in self.ledger.runs())

    def evaluate(self, record):
        if record.state == "create_unresolved":
            return self.resolver.resolve(record)
        if record.state in ("tearing_down", "cleanup_unverified"):
            return self.teardown.run(record, record.teardown_reason)
        now = self.clock()
        reason = self.teardown_reason(record, now)
        if reason:
            return self.teardown.run(record, reason)
        if record.state == "provisioning":
            return self.check_boot(record, now)
        return record

    def teardown_reason(self, record, now):
        if record.cancel_requested:
            return "cancelled"
        if now >= record.deadline:
            return "deadline"
        if record.state != "running":
            return None
        report = self.channel.report(record.run_id)
        if report.finished:
            return "succeeded"
        seen = max(t for t in (report.last_seen, record.grace_from) if t is not None)
        if now - seen > timedelta(seconds=self.settings.heartbeat_grace_seconds):
            return "heartbeat_lost"
        return None

    def check_boot(self, record, now):
        droplet_id = min(record.resource_ids(), default=None)
        try:
            droplet = self.provider.get_droplet(droplet_id) if droplet_id else None
            gone = droplet is None
        except Exception:
            droplet, gone = None, False  # Unknown: only the boot timeout applies.
        if droplet is not None and droplet.status == "active":
            record.grace_from = now
            record.move("running", now)
            self.ledger.save(record)
            return record
        booting_for = (now - record.created_at).total_seconds()
        if gone or booting_for > self.settings.boot_timeout_seconds:
            return self.teardown.run(record, "boot_failed")
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
        if not record.resources:
            # The crash may have come after the provider accepted the create.
            return self.resolver.hold(record, "restart_while_provisioning")
        return self.teardown.run(record, "restart_while_provisioning")

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
                    self.teardown.run(record, "controller_error")
