"""Delete a run's Droplets by ID, retry with backoff, and verify they are gone.

An ID counts as deleted once the provider acknowledged our DELETE for it, or once the
account action log shows a completed destroy action for that ID that started no earlier
than the run's creation, less the clock-skew allowance. Either way, it is gone only
when afterwards it reads 404 by ID and is missing from the run-tag listing. The
provider documents no read-after-write guarantee, so a 404 or an empty listing on its
own proves nothing. Powering a Droplet off never counts: it still bills.
"""

from datetime import timedelta

from .creation import CLOCK_SKEW_SECONDS
from .ledger import DestroyEvidence
from .settings import run_tag

# The lookup only adds evidence to a run already settled, so a slow log may not hold the loop.
LOOKUP_SECONDS = 60


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
        if record.state not in ("tearing_down", "cleanup_unverified"):
            self.begin(record, reason)
        # When this pass gives up. It also bounds the pass's reads of the action log.
        give_up = self.clock() + timedelta(seconds=self.settings.cleanup_verify_seconds)
        if record.state == "cleanup_unverified":
            # Already blocking launches; a later verified absence is the only way out.
            return self.settle(record) if self.sweep(record, give_up) else record
        attempt = 0
        while not self.sweep(record, give_up):
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

    def sweep(self, record, give_up):
        """One delete-then-verify pass; False leaves the run for another attempt.

        An ID with no acknowledged DELETE can clear only on the action log, so the log is
        read on the way, until `give_up` at most. With every DELETE acknowledged, nothing
        waits on the log before the run settles.
        """
        listed = self.owned(record.run_id)
        if listed is not None and not listed.issubset(record.resource_ids()):
            for droplet_id in listed:
                record.remember(droplet_id)
            self.ledger.save(record)
        deleted = [self.delete(record, droplet_id) for droplet_id in sorted(record.resource_ids())]
        if any(resource.acknowledged_at is None for resource in record.resources):
            self.corroborate(record, self.until(give_up))
        return listed is not None and all(deleted) and self.verified_absent(record)

    def until(self, deadline):
        """A read's budget: the seconds left before `deadline`."""
        return lambda: (deadline - self.clock()).total_seconds()

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
        # Durable before the request is sent, so a crash mid-request leaves the attempt known.
        record.resource(droplet_id).delete_requested_at = self.clock()
        self.ledger.save(record)
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

    def corroborate(self, record, time_left):
        """Read the action log, within `time_left`, and record what it showed for each ID.

        Each ID records when the read ended, and how: found, not_found, failed, or
        timed_out when it ran out of time. A read cut off shows nothing, so only a destroy
        read in time can count. A completed destroy, once recorded, is kept. A later read
        may fail, run out, no longer list it, or list only a failed or unfinished action
        for the ID, and none of these undoes it.
        """
        if not record.resources:
            return
        outcome = None
        try:
            actions = self.provider.list_destroy_actions(time_left=time_left)
        except Exception as error:
            # NO_TIME_LEFT is the provider's code for a read that ran out of time.
            actions, outcome = [], "timed_out" if str(error) == "NO_TIME_LEFT" else "failed"
        checked_at = self.clock()
        for resource in record.resources:
            found = [action for action in actions if action.resource_id == resource.id]
            recorded = resource.destroy_evidence
            if found and (recorded is None or recorded.status != "completed"):
                best = max(found, key=lambda action: (action.status == "completed",
                                                      action.started_at))
                resource.destroy_evidence = DestroyEvidence(
                    action_id=best.id, resource_id=best.resource_id, status=best.status,
                    started_at=best.started_at)
            resource.destroy_checked_at = checked_at
            resource.destroy_check = outcome or ("found" if found else "not_found")
        self.ledger.save(record)

    def verified_absent(self, record):
        """Deleted by acknowledgment or by the action log, then a 404 and no run-tag listing."""
        if record.released:
            # An ID we may not delete has no DELETE of ours behind it, so it never clears.
            return False
        if not all(self.deleted(record, resource) for resource in record.resources):
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

    def deleted(self, record, resource):
        """Our DELETE was acknowledged, or the action log shows this ID's completed destroy.

        A destroy that started before the run existed, less the clock-skew allowance,
        cannot be ours.
        """
        if resource.acknowledged_at is not None:
            return True
        evidence = resource.destroy_evidence
        earliest = record.created_at - timedelta(seconds=CLOCK_SKEW_SECONDS)
        return (evidence is not None
                and evidence.status == "completed"
                and evidence.resource_id == resource.id
                and evidence.started_at >= earliest)

    def settle(self, record):
        """Charge the budget for the time actually elapsed, now that nothing bills, then
        look the run's IDs up in the action log once, which changes nothing settled."""
        record.verified_at = self.clock()
        hours = (record.verified_at - record.created_at).total_seconds() / 3600
        record.settled_usd = round(max(hours, 0) * self.settings.hourly_price_usd, 6)
        record.move("torn_down", record.verified_at, record.teardown_reason)
        self.ledger.save(record)
        # Never retried: a crash before it leaves a settled run with no lookup recorded.
        lookup_ends = self.clock() + timedelta(seconds=LOOKUP_SECONDS)
        self.corroborate(record, self.until(lookup_ends))
        return record

    def mark_unverified(self, record):
        at = self.clock()
        record.move("cleanup_unverified", at, record.teardown_reason)
        self.ledger.save(record)
        return record

    def backoff(self, attempt):
        return min(self.settings.retry_base_seconds * 2**attempt, self.settings.retry_max_seconds)
