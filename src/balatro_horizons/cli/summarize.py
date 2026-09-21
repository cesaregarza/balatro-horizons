"""Retrospective decision-summary command."""

from balatro_horizons.evaluation.reports import scan
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.summary import render_decisions
from balatro_horizons.storage.journal import Store, atomic_json


def run_summarize_command(args):
    """Write an immutable JSON ledger and optional Markdown rendering."""
    outputs = [args.output, args.markdown_output]
    if any(path is not None and path.exists() for path in outputs):
        raise ValueError("SUMMARY_OUTPUT_EXISTS")
    if args.markdown_output and args.output.resolve() == args.markdown_output.resolve():
        raise ValueError("SUMMARY_OUTPUTS_MUST_DIFFER")

    store = Store(args.data_dir)
    result = build_summary(store, args.episode_id)
    markdown = None
    if args.markdown_output:
        markdown = render_decisions(result)
        scan({"markdown": markdown}, [store.manifest(args.episode_id, True).get("seed")])
    atomic_json(args.output, result, immutable=True)
    if markdown is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown_output.open("x", encoding="utf8", newline="") as stream:
            stream.write(markdown)
    return {
        "episode_id": args.episode_id,
        "recorded_actions": len(result["actions"]),
        "output": str(args.output),
        "markdown_output": str(args.markdown_output) if args.markdown_output else None,
    }
