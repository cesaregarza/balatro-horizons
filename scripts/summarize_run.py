#!/usr/bin/env python3
"""Group one public run into rounds, purchases and an action ledger.

No game or provider is contacted. Reading a whole run records review exposure.
"""

import argparse
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.decision_ledger import summarize as summarize
from balatro_horizons.review.decision_ledger import summary_input as summary_input
from balatro_horizons.storage.journal import Store, atomic_json


def latest_model_episode(store, model):
    """Select by immutable manifest creation time, never SQLite row order."""
    candidates = []
    for path in (store.root / "public_runs").glob("*/manifest.json"):
        manifest = store.manifest(path.parent.name)
        selected = manifest.get("config", {}).get("models", {}).get(manifest.get("agent"), {})
        if selected.get("model") == model:
            candidates.append((manifest["created_at"], manifest["episode_id"]))
    if not candidates:
        raise ValueError("NO_EPISODE_FOR_MODEL")
    return max(candidates)[1]


def run_status(store, eid):
    """Compact status from the verified journal, excluding response bodies and notes."""
    public = summary_input(store, eid)
    manifest, summary = public["manifest"], public["summary"] or {}
    model = manifest.get("config", {}).get("models", {}).get(manifest.get("agent"), {})
    counts = Counter(event["type"] for event in public["events"])
    result = {
        "episode_id": eid,
        "created_at": manifest.get("created_at"),
        "model": model.get("model"),
        "terminal": public["summary"] is not None,
        "journal_head": public["journal_head"],
        "last_timestamp": public["last_timestamp"],
        "observations": counts["observation"],
        "action_commits": counts["action_commit"],
        "provider_requests": counts["provider_request"],
        "provider_responses": counts["provider_response"],
    }
    for key in ("outcome", "reason", "evidence_kind"):
        value = summary.get(key)
        result[key] = (
            value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value)
            else None
        )
    for key in ("cost_usd", "provider_calls", "committed_actions"):
        result[key] = summary.get(key)
    latest = next((event for event in reversed(public["events"]) if event["type"] == "observation"), None)
    if latest:
        observation = latest["payload"]
        decision = observation["observation_id"]
        from balatro_horizons.agents.frozen import restore_protocol
        from balatro_horizons.engine.certification import require_checkpoint_certificate

        continuation = {"decision": decision, "phase": observation["phase"],
                        "progress": observation["state"]["progress"]}
        path = store.episode_path(eid, True) / f"checkpoint-{decision}.json"
        continuation["checkpoint_saved"] = path.is_file()
        if path.is_file():
            checkpoint = json.loads(path.read_text())
            continuation.update(checkpoint_cost=checkpoint.get("cost"), checkpoint_calls=checkpoint.get("calls"))
            for name, check in (
                ("protocol", lambda: restore_protocol(store, checkpoint)),
                ("restoration", lambda: require_checkpoint_certificate(store, eid, decision)),
            ):
                try:
                    check()
                    continuation[name] = "verified"
                except (ValueError, FileNotFoundError) as error:
                    code = str(error)
                    continuation[name] = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code) else "UNAVAILABLE"
        result["continuation"] = continuation
    context = summary.get("cost_context", {})
    result["cost_context"] = {key: context[key] for key in (
        "episode_cap_usd", "episode_committed_usd", "campaign_cap_usd",
        "campaign_committed_usd", "required_usd", "unsettled_usd",
    ) if key in context}
    scan(result, [store.manifest(eid, True).get("seed")])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--markdown-output", type=Path, help="Also write an ante-grouped decision recap"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    for path in (args.output, args.markdown_output):
        if path is not None and path.exists():
            parser.error("Output already exists; choose a new filename")
    if args.markdown_output and args.output.resolve() == args.markdown_output.resolve():
        parser.error("JSON and Markdown outputs must be different files")
    store = Store(args.data_dir)
    result = build_summary(store, args.episode_id)
    markdown = None
    if args.markdown_output:
        from decision_summary import render_decisions

        markdown = render_decisions(result)
        scan({"markdown": markdown}, [store.manifest(args.episode_id, True).get("seed")])
    atomic_json(args.output, result, immutable=True)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown_output.open("x", encoding="utf8") as stream:
            stream.write(markdown)
    print(f"Recorded {len(result['actions'])} actions in {args.output}")


if __name__ == "__main__":
    main()
