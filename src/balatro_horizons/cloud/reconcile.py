"""The independent reconciler: safe to run from a timer while the controller is down."""

import re
import time

from .controller import utc_now
from .ledger import RUN_ID_PATTERN, RunRecord
from .settings import run_id_from_tags
from .teardown import Teardown

RECONCILE_LOCK_WAIT_SECONDS = 30


def stale_reason(record, now, settings):
    """Why the reconciler may tear a run down without the controller, or None."""
    if record.state in ("tearing_down", "cleanup_unverified"):
        return record.teardown_reason
    if not record.live:
        return None
    if record.cancel_requested:
        return "cancelled"
    if now >= record.deadline:
        return "deadline"
    booting_for = (now - record.created_at).total_seconds()
    if record.state == "provisioning" and booting_for > settings.boot_timeout_seconds:
        return "boot_failed"
    return None


def reconcile(ledger, provider, settings, *, clock=utc_now, sleep=time.sleep,
              wait_seconds=RECONCILE_LOCK_WAIT_SECONDS):
    teardown = Teardown(ledger, provider, settings, clock, sleep)
    with ledger.lock(wait_seconds):
        torn = []
        for record in ledger.runs():
            reason = stale_reason(record, clock(), settings)
            if reason:
                torn.append(summary(teardown.run(record, reason)))
        try:
            owned = provider.list_droplets(settings.owner_tag)
        except Exception:
            # A failed listing proves nothing, so it deletes nothing.
            return {"runs": torn, "orphans": [], "orphan_scan": "LIST_FAILED"}
        orphans = []
        for droplet_id, run_id in orphan_droplets(ledger, owned, settings):
            record = adopt(ledger, run_id, droplet_id, clock(), settings)
            orphans.append(summary(teardown.run(record, "orphan")) | {"droplet_id": droplet_id})
        return {"runs": torn, "orphans": orphans, "orphan_scan": "COMPLETE"}


def orphan_droplets(ledger, droplets, settings):
    """Owner-tagged Droplets whose run tag names no live ledger run."""
    for droplet in droplets:
        run_id = run_id_from_tags(droplet.tags)
        if settings.owner_tag not in droplet.tags or run_id is None:
            continue
        if not re.fullmatch(RUN_ID_PATTERN, run_id):
            continue
        record = ledger.get(run_id)
        if record is None or not record.live:
            yield droplet.id, run_id


def adopt(ledger, run_id, droplet_id, now, settings):
    """Give an orphan a ledger run in teardown, so an unverified delete fails closed."""
    record = ledger.get(run_id) or RunRecord(
        run_id=run_id, state="tearing_down", created_at=now, deadline=now,
        reserved_usd=settings.worst_case_usd,
    )
    record.remember(droplet_id)
    record.teardown_reason = "orphan"
    record.teardown_started_at = now
    record.verified_at = None
    record.move("tearing_down", now, "orphan")
    ledger.save(record)
    return record


def summary(record):
    return {"run_id": record.run_id, "state": record.state, "reason": record.teardown_reason}
