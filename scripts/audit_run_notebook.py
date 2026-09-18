#!/usr/bin/env python3
"""Audit persistent notes and their actual delivery; no game or provider calls.

Example: .venv/bin/python scripts/audit_run_notebook.py --episode-id ID \
    --data-dir /path/to/data --output /tmp/notebook-audit.json
Whole-run inspection records retrospective exposure. Decision numbers are 1-based.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median

from decision_summary import cell

from balatro_horizons.agents.notebook import RunNotebook, used_characters
from balatro_horizons.config import ROOT
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import Store, atomic_json, locked


def delivered_context(body):
    for message in body.get("input", body.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(block.get("text", "") for block in content
                              if block.get("type") in ("text", "input_text"))
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except ValueError:
            continue
        if isinstance(value, dict) and "observation" in value:
            return value
    return {}


def analyze(events, manifest):
    # A branch needs inherited mutations; fail rather than treat them as missing.
    if manifest.get("parent") or manifest.get("parent_episode_id"):
        raise ValueError("NOTEBOOK_AUDIT_REQUIRES_UNBRANCHED_EPISODE")
    limit = manifest.get("config", {}).get("budgets", {}).get("memory_max_characters", 4096)
    notebook = RunNotebook(limit)
    mutations, requests, problems, actions, rejections = [], [], [], [], []
    observation, context, operation = {}, {}, {}
    maximum = 0
    context_count = 0
    for event in events:
        kind, payload = event["type"], event["payload"]
        if kind == "observation":
            observation = payload
        oid = event.get("observation_id")
        decision = (oid if oid is not None else observation.get("observation_id", -1)) + 1
        state = observation.get("state", {})
        where = {"decision": decision, "ante": state.get("progress", {}).get("ante"),
                 "phase": observation.get("phase"), "cash": state.get("resources", {}).get("money")}
        if kind == "agent_operation":
            operation = payload["operation"]
        elif kind == "run_note":
            old = notebook.entries.get(payload["key"])
            notebook.apply(payload)
            maximum = max(maximum, used_characters(notebook.entries))
            mutations.append({**where, "sequence": event["sequence"],
                              "revision": payload["revision"], "operation": payload["operation"],
                              "key": payload["key"], "previous_text": old,
                              "text": payload.get("text"),
                              "origin": "attached_to_action" if operation.get("kind") == "action"
                              else "standalone_helper",
                              "unchanged_text": payload.get("text") == old,
                              "notebook_characters": used_characters(notebook.entries)})
        elif kind == "agent_context":
            context_count += 1
            context = payload["context"]
            recorded = context.get("run_notebook", {})
            if (recorded.get("entries") != notebook.entries
                    or recorded.get("revision") != notebook.revision):
                problems.append({**where, "sequence": event["sequence"],
                                 "error": "JOURNALED_NOTES_CONTEXT_MISMATCH"})
        elif kind == "provider_request":
            delivered = delivered_context(payload["body"])
            recorded = delivered.get("run_notebook")
            if recorded is None or recorded != context.get("run_notebook"):
                problems.append({**where, "sequence": event["sequence"],
                                 "error": "PROVIDER_NOTEBOOK_MISMATCH"})
            recorded = recorded or {}
            memory = delivered.get("working_memory", {})
            if memory != context.get("working_memory", {}):
                problems.append({**where, "sequence": event["sequence"],
                                 "error": "PROVIDER_WORKING_MEMORY_MISMATCH"})
            requests.append({**where, "sequence": event["sequence"],
                             "revision": recorded.get("revision"),
                             "characters": used_characters(recorded.get("entries", {})),
                             "keys": list(recorded.get("entries", {})),
                             "working_memory_decisions": [frame["decision_id"] + 1
                                                          for frame in memory.get("frames", [])]})
        elif kind == "action_commit":
            actions.append({**where, "action": payload["action"]["type"],
                            "decision_note": payload.get("decision_note"),
                            "notebook_revision": notebook.revision,
                            "reroll_cost": state.get("resources", {}).get("shop_reroll_cost"),
                            "jokers": [card["label"] for card in state.get("jokers", [])]})
        elif kind == "action_rejected":
            rejections.append({**where, "code": payload.get("code")})
    summary = next((e["payload"] for e in reversed(events) if e["type"] == "terminal"), {})
    model = manifest.get("config", {}).get("models", {}).get(manifest.get("agent"), {})
    return {
        "episode_id": manifest["episode_id"], "created_at": manifest.get("created_at"),
        "model": model.get("model"), "interface": model.get("settings", {}).get("harness_interface"),
        "effort": model.get("settings", {}).get("reasoning_effort"),
        "evidence_kind": manifest.get("evidence_kind"),
        "journal_head": events[-1]["hash"] if events else None,
        "outcome": summary.get("outcome", "IN_PROGRESS"), "reason": summary.get("reason"),
        "final_ante": observation.get("state", {}).get("progress", {}).get("ante"),
        "committed_actions": len(actions), "provider_requests": len(requests),
        "context_count": context_count, "delivery_problems": problems,
        "updates": len(mutations), "updates_by_key": dict(Counter(m["key"] for m in mutations)),
        "origins": dict(Counter(m["origin"] for m in mutations)),
        "deletions": sum(m["operation"] == "delete_run_note" for m in mutations),
        "unchanged_rewrites": sum(m["unchanged_text"] for m in mutations),
        "character_limit": limit, "max_characters": maximum,
        "median_delivered_characters": median([r["characters"] for r in requests]) if requests else None,
        "empty_notebook_requests": sum(not r["keys"] for r in requests),
        "working_memory_frame_counts": dict(Counter(len(r["working_memory_decisions"]) for r in requests)),
        "final_notebook": notebook.snapshot(), "mutations": mutations,
        "requests": requests, "actions": actions, "rejections": rejections,
    }


def audit(store, eid):
    manifest = store.manifest(eid)
    with locked(store.episode_path(eid) / ".writer.lock"):
        events = store.events(eid)
    result = analyze(events, manifest)
    scan(result, [store.manifest(eid, True).get("seed")])
    ReviewService(store).expose(eid, "run_notebook_audit", max_event_seen=len(events)-1,
                              outcome_seen=any(e["type"] == "terminal" for e in events),
                              model_identity_seen=True)
    return result


def render(results):
    lines = ["# V7 run notebook audit", "",
             "Decision numbers match the dashboard (1-based). These are model-authored "
             "records, including predictions and mistakes, not verified game facts. "
             "Every notebook edit below was checked against subsequent contexts and "
             "actual provider request bodies. No game or model calls were made.", ""]
    for result in results:
        lines.extend([
            f"## {cell(result['model'])}", "",
            f"Episode: {cell(result['episode_id'])}. "
            f"{result['committed_actions']} actions; {result['provider_requests']} requests; "
            f"{cell(result['outcome'])} in Ante {result['final_ante']}.", "",
            f"Notebook: {result['updates']} writes, {result['deletions']} deletions, "
            f"{result['max_characters']}/{result['character_limit']} characters at peak. "
            f"Median delivered size: {result['median_delivered_characters']} characters. "
            f"Delivery discrepancies: {len(result['delivery_problems'])}.", "",
            "### Final notebook", "",
        ])
        for key, value in result["final_notebook"]["entries"].items():
            lines.extend([f"**{cell(key)}**: {cell(value)}", ""])
        lines.extend(["### Complete revision history", "",
                      "| Decision | Ante / phase | Cash before action | Key | Updated note |",
                      "| --- | --- | --- | --- | --- |"])
        for mutation in result["mutations"]:
            values = [mutation["decision"], f"{mutation['ante']} / {mutation['phase']}",
                      mutation["cash"], mutation["key"],
                      mutation["text"] if mutation["text"] is not None else "[deleted]"]
            lines.append("| " + " | ".join(cell(value) for value in values) + " |")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True, action="append")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, required=True, help="New JSON file; never overwritten")
    parser.add_argument("--markdown-output", type=Path, help="Optional complete readable revision history")
    args = parser.parse_args()
    for path in (args.output, args.markdown_output):
        if path is not None and path.exists():
            parser.error("Output already exists")
    if args.markdown_output and args.markdown_output.resolve() == args.output.resolve():
        parser.error("Output paths must differ")
    if not (args.data_dir / "public_runs").is_dir():
        parser.error("Data directory has no public_runs")
    store = Store(args.data_dir)
    results = [audit(store, eid) for eid in args.episode_id]
    atomic_json(args.output, results, immutable=True)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown_output.open("x", encoding="utf8") as stream:
            stream.write(render(results))
    for result in results:
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("mutations", "requests", "actions", "final_notebook")},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
