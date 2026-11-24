"""The status report `bh cloud status` prints and the dashboard shows: one function, two readers.

It reads only the durable records and the launch gate's own functions. It never builds a
provider client, and reading creates no directory and no lock.
"""

from pathlib import Path

import yaml

from .gate import blockers, exposure_usd, launch_refusals, remaining_budget_usd
from .ledger import Ledger
from .settings import load_settings, utc_now

ALERT_MESSAGES = {
    "CLEANUP_UNVERIFIED": "Cleanup of run {run_id} is unverified. New launches are refused "
                          "until a later sweep verifies its Droplets are gone.",
    "CREATE_UNRESOLVED": "The create for run {run_id} may have succeeded. New launches are "
                         "refused until its Droplet is found and its deletion verified; "
                         "empty reads never clear it.",
    "UNMATCHED_DROPLET": "Droplet {droplet_id} is one this controller may not delete: it lost "
                         "its run's tags, or carries the owner tag with no usable run tag. New "
                         "launches are refused, and no read clears it.",
    "INVENTORY_UNVERIFIED": "The last read of the owned inventory failed. New launches are "
                            "refused until a complete read.",
}


class CloudStatusError(Exception):
    """A record or the settings could not be read; the code names which."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def active_alerts(ledger):
    """One alert per durable blocker, from the same records the launch gate reads."""
    return [blocker | {"since": blocker["since"].isoformat(),
                       "message": ALERT_MESSAGES[blocker["code"]].format(**blocker)}
            for blocker in blockers(ledger)]


def status_report(ledger, settings, now):
    runs, inventory = ledger.runs(), ledger.inventory()
    refusals = launch_refusals(ledger, settings, now)
    return {
        "read_at": now.isoformat(),
        "launches": "REFUSED" if refusals else "ALLOWED",
        "refusals": refusals,
        "alerts": active_alerts(ledger),
        "inventory": None if inventory is None else {
            "complete": inventory.complete, "checked_at": inventory.at.isoformat()},
        "budget_usd": {
            "total": settings.infrastructure_budget_usd,
            "remaining": round(remaining_budget_usd(runs, settings, now), 6),
            "worst_case_per_run": round(settings.worst_case_usd, 6),
        },
        "runs": [{
            "run_id": record.run_id, "state": record.state,
            "created_at": record.created_at.isoformat(), "deadline": record.deadline.isoformat(),
            "teardown_reason": record.teardown_reason,
            # The gate's own estimate, so the runs add up to the total less the remainder.
            "cost_usd": round(exposure_usd(record, settings, now), 6),
            "reserved_usd": record.reserved_usd, "settled_usd": record.settled_usd,
        } for record in runs],
    }


def read_status(data_dir):
    """The report for a data directory, read fresh from disk on every call.

    A record or settings file that cannot be read is an error with a named code. It is
    never reported as an empty list, which would read as "nothing is running".
    """
    try:
        settings = load_settings()
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise CloudStatusError("CLOUD_SETTINGS_INVALID") from error
    try:
        return status_report(Ledger(Path(data_dir) / "cloud"), settings, utc_now())
    except (OSError, ValueError) as error:
        raise CloudStatusError("CLOUD_LEDGER_UNREADABLE") from error
