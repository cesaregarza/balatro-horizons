"""The launch gate: durable blockers, concurrency and the infrastructure budget.

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


BLOCKING_STATES = {"cleanup_unverified": "CLEANUP_UNVERIFIED",
                   "create_unresolved": "CREATE_UNRESOLVED"}


def blockers(ledger):
    """The durable records that refuse launches; status shows exactly these as alerts.

    Each is read from the record that commits it, so no second write can be lost.
    Each may be billing unnoticed, and there is no override.
    """
    found = [{"code": BLOCKING_STATES[r.state], "run_id": r.run_id, "droplet_id": None,
              "since": r.history[-1].at} for r in ledger.runs() if r.state in BLOCKING_STATES]
    found += [{"code": "UNMATCHED_DROPLET", "run_id": None, "droplet_id": w.droplet_id,
               "since": w.found_at} for w in ledger.unmatched()]
    inventory = ledger.inventory()
    if inventory is not None and not inventory.complete:
        found.append({"code": "INVENTORY_UNVERIFIED", "run_id": None, "droplet_id": None,
                      "since": inventory.at})
    return found


def launch_refusals(ledger, settings, now):
    """Every blocker's code, then the capacity limits, which are not alerts."""
    reasons = list(dict.fromkeys(blocker["code"] for blocker in blockers(ledger)))
    runs = ledger.runs()
    if sum(record.live for record in runs) >= settings.max_concurrent_workers:
        reasons.append("CONCURRENCY_LIMIT")
    if settings.worst_case_usd > remaining_budget_usd(runs, settings, now):
        reasons.append("INFRASTRUCTURE_BUDGET")
    return reasons
