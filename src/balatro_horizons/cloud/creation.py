"""Holding a create whose outcome is unknown, and releasing it only on sight.

A lost response, timeout, dropped connection or 5xx may follow an accepted create,
and so may a controller crash before the Droplet ID is recorded. A fresh Droplet
can be missing from listings for a while, and the action log can show its create
in progress with no resource ID yet. Empty reads therefore prove nothing, however
long they last. The run keeps its reservation and its slot, and blocks launches,
until its Droplet is found, by run tag, by exact name, or by an ID the action log
names, carrying both tags, and its deletion is verified.

Every teardown path goes through `CreateResolver.tear_down`, so none of them can
settle such a run on empty listings and no recorded IDs. The one exception is an
adopted orphan, which always has its Droplet ID recorded first.
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


def create_outcome_unknown(record):
    """A create was attempted, and neither a Droplet ID nor a rejection is recorded."""
    if record.create_attempted_at is None or record.create_rejected:
        return False
    return not record.resources and not record.released


class CreateResolver:
    def __init__(self, ledger, provider, settings, teardown, clock):
        self.ledger = ledger
        self.provider = provider
        self.settings = settings
        self.teardown = teardown
        self.clock = clock

    def tear_down(self, record, reason):
        """The one way in to teardown; a create with no known outcome is held instead."""
        if record.state == "create_unresolved":
            return self.resolve(record)
        if create_outcome_unknown(record):
            return self.hold(record, reason)
        return self.teardown.run(record, reason)

    def hold(self, record, reason):
        now = self.clock()
        record.teardown_reason = reason
        record.move("create_unresolved", now, reason)
        self.ledger.save(record)
        self.ledger.record_alert("CREATE_UNRESOLVED", now, run_id=record.run_id)
        return self.resolve(record)

    def resolve(self, record):
        """Tear down what is found; anything less, including nothing, keeps the hold."""
        found = self.listed(record) | self.named_by_actions(record)
        return self.tear(record, found) if found else record

    def listed(self, record):
        try:
            tagged = self.provider.list_droplets(run_tag(record.run_id))
            named = self.provider.find_droplets_by_name(droplet_name(record.run_id))
        except Exception:
            return set()
        return {d.id for d in tagged + named if self.owned(d, record.run_id)}

    def named_by_actions(self, record):
        """Our Droplets among those the action log names by ID since the attempt.

        An in-progress create with no ID yet, or an ID that answers 404, names nothing.
        """
        since = record.create_attempted_at - timedelta(seconds=CLOCK_SKEW_SECONDS)
        try:
            actions = self.provider.list_create_actions(since)
            current = [self.provider.get_droplet(action.resource_id) for action in actions
                       if action.resource_id is not None]
        except Exception:
            return set()
        # A Droplet with another owner's tags is never touched.
        return {d.id for d in current if d is not None and self.owned(d, record.run_id)}

    def owned(self, droplet, run_id):
        return self.settings.owner_tag in droplet.tags and run_tag(run_id) in droplet.tags

    def tear(self, record, droplet_ids):
        for droplet_id in droplet_ids:
            record.remember(droplet_id)
        return self.teardown.run(record, record.teardown_reason)
