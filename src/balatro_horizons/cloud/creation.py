"""Resolving a create whose outcome is unknown, only on positive evidence.

A lost response, timeout, dropped connection or 5xx may follow an accepted create,
and a fresh Droplet can be missing from listings for a while. An empty listing
right after such a create therefore proves nothing. The run holds its reservation
and its slot, and blocks launches, until one of these holds:

- its Droplet is found by run tag or by its exact name, and deleted and verified; or
- the settle window has passed, the account action log has been read completely back
  to before the attempt, every Droplet create in it is resolved by ID, and the
  run-tag and name listings are empty.
"""

import re
from datetime import timedelta

from .provider import ProviderError
from .settings import droplet_name, run_tag

# Our clock and the provider's can disagree; read the action log this far further back.
CLOCK_SKEW_SECONDS = 300


def definite_rejection(error):
    """A 4xx means the API refused the request before acting; nothing was made."""
    return isinstance(error, ProviderError) and re.fullmatch(r"HTTP_4\d\d", str(error))


class CreateResolver:
    def __init__(self, ledger, provider, settings, teardown, clock):
        self.ledger = ledger
        self.provider = provider
        self.settings = settings
        self.teardown = teardown
        self.clock = clock

    def hold(self, record, reason):
        now = self.clock()
        record.teardown_reason = reason
        record.move("create_unresolved", now, reason)
        self.ledger.save(record)
        self.ledger.record_alert("CREATE_UNRESOLVED", now, run_id=record.run_id)
        return self.resolve(record)

    def resolve(self, record):
        ours, strangers = self.find(record)
        if ours is None:
            return record
        if ours:
            return self.tear(record, ours)
        waited = (self.clock() - record.create_attempted_at).total_seconds()
        if strangers or waited < self.settings.create_settle_seconds:
            return record
        created = self.created_in_window(record)
        return record if created is None else self.tear(record, created)

    def find(self, record):
        """Our Droplets by run tag or exact name, and whether a stranger has the name."""
        tag = run_tag(record.run_id)
        try:
            tagged = self.provider.list_droplets(tag)
            named = self.provider.find_droplets_by_name(droplet_name(record.run_id))
        except Exception:
            return None, False
        ours = {d.id for d in tagged + named if self.owned(d, record.run_id)}
        return ours, any(d.id not in ours for d in named)

    def created_in_window(self, record):
        since = record.create_attempted_at - timedelta(seconds=CLOCK_SKEW_SECONDS)
        try:
            actions = self.provider.list_create_actions(since)
            current = [self.provider.get_droplet(a.resource_id) for a in actions]
        except Exception:
            return None
        # A 404 is gone; a Droplet with another owner's tags is never touched.
        return {d.id for d in current if d is not None and self.owned(d, record.run_id)}

    def owned(self, droplet, run_id):
        return self.settings.owner_tag in droplet.tags and run_tag(run_id) in droplet.tags

    def tear(self, record, droplet_ids):
        for droplet_id in droplet_ids:
            record.remember(droplet_id)
        return self.teardown.run(record, record.teardown_reason)
