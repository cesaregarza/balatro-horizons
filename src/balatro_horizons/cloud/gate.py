"""The launch gate: unverified cleanup, concurrency and the infrastructure budget.

Infrastructure money is bounded here on its own, apart from model spend.
"""


def exposure_usd(record, settings, now):
    """Settled cost once verified gone; until then the larger of reserved and elapsed."""
    if record.state == "torn_down":
        return record.settled_usd or 0.0
    elapsed_hours = (now - record.created_at).total_seconds() / 3600
    return max(record.reserved_usd, elapsed_hours * settings.hourly_price_usd)


def remaining_budget_usd(runs, settings, now):
    committed = sum(exposure_usd(record, settings, now) for record in runs)
    return settings.infrastructure_budget_usd - committed


def launch_refusals(runs, unmatched, settings, now):
    reasons = []
    # Fail closed: an unverified cleanup may still be billing, and no override exists.
    if any(record.state == "cleanup_unverified" for record in runs):
        reasons.append("CLEANUP_UNVERIFIED")
    # An owned Droplet this controller cannot attribute may also be billing unnoticed.
    if any(record.gone_at is None for record in unmatched):
        reasons.append("UNMATCHED_DROPLET")
    if sum(record.live for record in runs) >= settings.max_concurrent_workers:
        reasons.append("CONCURRENCY_LIMIT")
    if settings.worst_case_usd > remaining_budget_usd(runs, settings, now):
        reasons.append("INFRASTRUCTURE_BUDGET")
    return reasons
