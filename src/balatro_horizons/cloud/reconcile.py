"""The independent reconciler: safe to run from a timer while the controller is down."""

import re
import time

from .controller import utc_now
from .ledger import RUN_ID_PATTERN, RunRecord, UnmatchedDroplet
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
            listed = provider.list_droplets(settings.owner_tag)
        except Exception:
            # A failed listing proves nothing, so it deletes nothing.
            return {"runs": torn, "orphans": [], "unmatched": [], "orphan_scan": "LIST_FAILED"}
        owned = [droplet for droplet in listed if settings.owner_tag in droplet.tags]
        orphans = []
        for droplet, run_id in orphan_droplets(ledger, owned):
            record = adopt(ledger, run_id, droplet, clock(), settings)
            orphans.append(summary(teardown.run(record, "orphan")) | {"droplet_id": droplet.id})
        unmatched = watch_unmatched(ledger, provider, owned, clock())
        return {"runs": torn, "orphans": orphans, "unmatched": unmatched,
                "orphan_scan": "COMPLETE"}


def usable_run_id(droplet):
    run_id = run_id_from_tags(droplet.tags)
    return run_id if run_id is not None and re.fullmatch(RUN_ID_PATTERN, run_id) else None


def orphan_droplets(ledger, owned):
    """Owner-tagged Droplets whose run tag names no live ledger run."""
    for droplet in owned:
        run_id = usable_run_id(droplet)
        if run_id is None:
            continue
        record = ledger.get(run_id)
        if record is None or not record.live:
            yield droplet, run_id


def adopt(ledger, run_id, droplet, now, settings):
    """Give an orphan a ledger run in teardown, so an unverified delete fails closed."""
    # Settle from when the provider made it; only an unknown age falls back to now.
    record = ledger.get(run_id) or RunRecord(
        run_id=run_id, state="tearing_down", created_at=droplet.created_at or now,
        deadline=now, reserved_usd=settings.worst_case_usd,
    )
    record.remember(droplet.id)
    record.teardown_reason = "orphan"
    record.teardown_started_at = now
    record.verified_at = None
    record.move("tearing_down", now, "orphan")
    ledger.save(record)
    return record


def watch_unmatched(ledger, provider, owned, now):
    """Alert on owned Droplets with no usable run tag, and fail closed until they are gone.

    They are never deleted: without a run tag the both-tags filter cannot confirm them.
    """
    listed = {droplet.id for droplet in owned if usable_run_id(droplet) is None}
    records = {record.droplet_id: record for record in ledger.unmatched()}
    for droplet_id in sorted(listed):
        record = records.get(droplet_id)
        if record is None or record.gone_at is not None:
            records[droplet_id] = UnmatchedDroplet(droplet_id=droplet_id, found_at=now)
            ledger.save_unmatched(records[droplet_id])
            ledger.record_alert("UNMATCHED_DROPLET", now, droplet_id=droplet_id)
    for record in records.values():
        if record.gone_at is None and record.droplet_id not in listed:
            if confirmed_gone(provider, record.droplet_id):
                record.gone_at = now
                ledger.save_unmatched(record)
    return [{"droplet_id": record.droplet_id,
             "state": "present" if record.gone_at is None else "gone"}
            for record in records.values() if record.gone_at is None or record.gone_at == now]


def confirmed_gone(provider, droplet_id):
    try:
        return provider.get_droplet(droplet_id) is None
    except Exception:
        return False


def summary(record):
    return {"run_id": record.run_id, "state": record.state, "reason": record.teardown_reason}
