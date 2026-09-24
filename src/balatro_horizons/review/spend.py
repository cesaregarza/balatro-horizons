"""Episode-only display accounting; never changes execution or budget admission."""

from math import fsum, isclose, isfinite


def _amount(value):
    return value if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def _total(values):
    amounts = list(values)
    return None if None in amounts else fsum(amounts)


def run_spend(events, summary, *, restoration=None):
    """Project numbers only, preserving unknown usage and authoritative terminals.

    Response costs are harness estimates, potentially reservation fallbacks, not
    invoice-confirmed charges. Reservation/request pairs describe one attempt.
    """
    reserved, responses = {}, {}
    for event in events:
        kind, payload, request_id = event["type"], event["payload"], event.get("request_id")
        if kind in ("provider_reservation", "provider_request"):
            reserved[request_id] = _amount(payload.get("reserved_usd"))
        elif kind == "provider_response":
            responses[request_id] = _amount(payload.get("cost_usd"))
    recorded = _total(responses.values())
    pending = _total(cost for key, cost in reserved.items() if key not in responses)
    journal_total = _total((recorded, pending))
    terminal_total = _amount((summary or {}).get("cost_usd"))
    accounted = terminal_total if terminal_total is not None else journal_total
    if terminal_total is not None and (
        journal_total is None or not isclose(terminal_total, journal_total, rel_tol=0, abs_tol=1e-9)
    ):
        # Old or recovered runs may lack the journal detail for a trustworthy split.
        recorded = pending = None
    result = {"accounted_usd": accounted, "response_usd": recorded, "reserved_usd": pending}
    if restoration is not None:
        prior = _amount(restoration.get("prior_cost_usd"))
        result.update(prior_usd=prior, combined_usd=_total((prior, accounted)))
    return result
