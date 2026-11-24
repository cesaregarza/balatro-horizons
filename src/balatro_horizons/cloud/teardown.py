"""Delete a run's Droplets by ID, retry with backoff, and verify they are gone.

Verified means both a 404 for every recorded ID and an empty listing for the run tag.
Powering a Droplet off never counts: it still bills.
"""

from datetime import timedelta

from .settings import run_tag


class Teardown:
    def __init__(self, ledger, provider, settings, clock, sleep):
        self.ledger = ledger
        self.provider = provider
        self.settings = settings
        self.clock = clock
        self.sleep = sleep

    def run(self, record, reason):
        """Tear a run down; repeated delivery after verification is a no-op."""
        if record.state == "torn_down":
            return record
        if record.state == "cleanup_unverified":
            # Already alerted; a later verified absence is the only way out.
            return self.settle(record) if self.sweep(record) else record
        if record.state != "tearing_down":
            self.begin(record, reason)
        give_up = self.clock() + timedelta(seconds=self.settings.cleanup_verify_seconds)
        attempt = 0
        while not self.sweep(record):
            if self.clock() >= give_up:
                return self.mark_unverified(record)
            self.sleep(self.backoff(attempt))
            attempt += 1
        return self.settle(record)

    def begin(self, record, reason):
        record.teardown_reason = reason
        record.teardown_started_at = self.clock()
        record.move("tearing_down", record.teardown_started_at, reason)
        self.ledger.save(record)

    def sweep(self, record):
        """One delete-then-verify pass; False leaves the run for another attempt."""
        listed = self.owned(record.run_id)
        if listed is not None and not listed.issubset(record.resource_ids()):
            for droplet_id in listed:
                record.remember(droplet_id)
            self.ledger.save(record)
        deleted = [self.delete(record, droplet_id) for droplet_id in sorted(record.resource_ids())]
        return listed is not None and all(deleted) and self.verified_absent(record)

    def owned(self, run_id):
        """IDs listed under the run tag that also carry this controller's owner tag."""
        tag = run_tag(run_id)
        try:
            droplets = self.provider.list_droplets(tag)
        except Exception:
            return None
        return {d.id for d in droplets if tag in d.tags and self.settings.owner_tag in d.tags}

    def delete(self, record, droplet_id):
        """Delete only what still carries both tags; a known ID alone is not ownership."""
        try:
            current = self.provider.get_droplet(droplet_id)
        except Exception:
            return False
        if current is None:
            return True
        if not {self.settings.owner_tag, run_tag(record.run_id)} <= set(current.tags):
            record.release(droplet_id)
            self.ledger.save(record)
            self.ledger.note_unmatched(droplet_id, self.clock())
            return True
        try:
            self.provider.delete_droplet(droplet_id)
        except Exception:
            return False
        return True

    def verified_absent(self, record):
        try:
            gone_by_id = all(
                self.provider.get_droplet(droplet_id) is None
                for droplet_id in record.resource_ids()
            )
        except Exception:
            return False
        return gone_by_id and self.owned(record.run_id) == set()

    def settle(self, record):
        """Charge the budget for the time actually elapsed, now that nothing bills."""
        record.verified_at = self.clock()
        hours = (record.verified_at - record.created_at).total_seconds() / 3600
        record.settled_usd = round(max(hours, 0) * self.settings.hourly_price_usd, 6)
        record.move("torn_down", record.verified_at, record.teardown_reason)
        self.ledger.save(record)
        return record

    def mark_unverified(self, record):
        at = self.clock()
        record.move("cleanup_unverified", at, record.teardown_reason)
        self.ledger.save(record)
        self.ledger.record_alert("CLEANUP_UNVERIFIED", at, run_id=record.run_id)
        return record

    def backoff(self, attempt):
        return min(self.settings.retry_base_seconds * 2**attempt, self.settings.retry_max_seconds)
