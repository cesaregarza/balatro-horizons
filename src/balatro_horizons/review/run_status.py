"""Sanitized continuation readiness consumed by the budget command's plan."""

import json
import re
from collections import Counter

from balatro_horizons.evaluation.reports import scan
from balatro_horizons.evidence.certification import (
    require_checkpoint_certificate,
    require_continuation_probe_certificate,
)
from balatro_horizons.harness.context.freeze import restore_protocol
from balatro_horizons.review.decision_ledger import summary_input


def _safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value) else None


def _continuation_status(store, eid, observation, summary):
    decision = observation["observation_id"]
    result = {"decision": decision, "phase": observation["phase"],
              "progress": observation["state"]["progress"]}
    path = store.episode_path(eid, True) / f"checkpoint-{decision}.json"
    result["checkpoint_saved"] = path.is_file()
    if not path.is_file():
        return result
    checkpoint = json.loads(path.read_text())
    result.update(checkpoint_cost=checkpoint.get("cost"), checkpoint_calls=checkpoint.get("calls"))
    # Cost-stopped roots need the probe pointer; an ordinary replay pass cannot
    # authorize funding an unobserved future at a terminal boundary.
    certificate = (require_continuation_probe_certificate
                   if summary.get("outcome") in ("BUDGET_EXHAUSTED", "CAMPAIGN_INTERRUPTED")
                   else require_checkpoint_certificate)
    for name, check in (
        ("protocol", lambda: restore_protocol(store, checkpoint)),
        ("restoration", lambda: certificate(store, eid, decision)),
    ):
        try:
            check()
            result[name] = "verified"
        except (ValueError, FileNotFoundError) as error:
            result[name] = _safe_code(str(error)) or "UNAVAILABLE"
    return result


def run_status(store, eid):
    """Describe saved versus certified state without exposing checkpoint bodies."""
    public = summary_input(store, eid)
    manifest, summary = public["manifest"], public["summary"] or {}
    model = manifest.get("config", {}).get("models", {}).get(manifest.get("agent"), {})
    counts = Counter(event["type"] for event in public["events"])
    result = {
        "episode_id": eid, "created_at": manifest.get("created_at"), "model": model.get("model"),
        "terminal": public["summary"] is not None, "journal_head": public["journal_head"],
        "last_timestamp": public["last_timestamp"], "observations": counts["observation"],
        "action_commits": counts["action_commit"], "provider_requests": counts["provider_request"],
        "provider_responses": counts["provider_response"],
    }
    for key in ("outcome", "reason", "evidence_kind"):
        result[key] = _safe_code(summary.get(key))
    for key in ("cost_usd", "provider_calls", "committed_actions"):
        result[key] = summary.get(key)
    latest = next((event for event in reversed(public["events"]) if event["type"] == "observation"), None)
    if latest:
        result["continuation"] = _continuation_status(store, eid, latest["payload"], summary)
    context = summary.get("cost_context", {})
    result["cost_context"] = {key: context[key] for key in (
        "episode_cap_usd", "episode_committed_usd", "campaign_cap_usd",
        "campaign_committed_usd", "required_usd", "unsettled_usd",
    ) if key in context}
    scan(result, [store.manifest(eid, True).get("seed")])
    return result
