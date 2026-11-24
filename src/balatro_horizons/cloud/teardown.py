"""Delete a run's Droplets by ID, retry with backoff, and verify they are gone.

The controller believes a Droplet is gone only after the provider acknowledged our
DELETE for it. An ID is gone when a fresh read showed it with both tags, our DELETE
for it was acknowledged, and afterwards it reads 404 by ID and is missing from the
run-tag listing. The provider documents no read-after-write guarantee, so a 404 or
an empty listing before the acknowledgment proves nothing. Powering a Droplet off
never counts: it still bills.
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
            # Already blocking launches; a later verified absence is the only way out.
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

    def listing(self, run_id):
        """Droplets listed under the run tag, or None when the listing failed."""
        try:
            return self.provider.list_droplets(run_tag(run_id))
        except Exception:
            return None

    def owned(self, run_id):
        """IDs listed under the run tag that also carry this controller's owner tag."""
        droplets = self.listing(run_id)
        if droplets is None:
            return None
        tags = {self.settings.owner_tag, run_tag(run_id)}
        return {d.id for d in droplets if tags <= set(d.tags)}

    def delete(self, record, droplet_id):
        """Delete what a fresh read shows with both tags; False when this pass must retry."""
        try:
            current = self.provider.get_droplet(droplet_id)
        except Exception:
            return False
        if current is None:
            # Nothing to delete now. Whether it is gone is for verification to decide,
            # and before our DELETE is acknowledged, a 404 proves nothing.
            return True
        if not {self.settings.owner_tag, run_tag(record.run_id)} <= set(current.tags):
            self.lose_ownership(record, droplet_id)
            return False
        try:
            acknowledged = self.provider.delete_droplet(droplet_id)
        except Exception:
            return False
        if not acknowledged:
            return False
        # Durable before any later read is trusted.
        record.resource(droplet_id).acknowledged_at = self.clock()
        self.ledger.save(record)
        return True

    def lose_ownership(self, record, droplet_id):
        """A known ID without both tags is never deleted; the unmatched watch owns it.

        The watch is written first, so a crash between the two writes leaves the ID in
        both places rather than in neither, and the next sweep repeats this step.
        """
        self.ledger.note_unmatched(droplet_id, self.clock())
        record.release(droplet_id)
        self.ledger.save(record)

    def verified_absent(self, record):
        """Gone means our DELETE was acknowledged, then a 404 by ID and no run-tag listing."""
        if record.released:
            # An ID we may not delete has no acknowledgment to wait for, so it never clears.
            return False
        if any(resource.acknowledged_at is None for resource in record.resources):
            return False
        try:
            gone_by_id = all(
                self.provider.get_droplet(droplet_id) is None
                for droplet_id in record.resource_ids()
            )
        except Exception:
            return False
        droplets = self.listing(record.run_id)
        if droplets is None:
            return False
        tags = {self.settings.owner_tag, run_tag(record.run_id)}
        listed = {d.id for d in droplets if d.id in record.resource_ids() or tags <= set(d.tags)}
        return gone_by_id and not listed

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
        return record

    def backoff(self, attempt):
        return min(self.settings.retry_base_seconds * 2**attempt, self.settings.retry_max_seconds)
