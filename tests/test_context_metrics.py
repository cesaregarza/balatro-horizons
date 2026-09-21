"""The context size gate detects long files and functions."""

import importlib.util

from balatro_horizons.config import ROOT

SPEC = importlib.util.spec_from_file_location("context_metrics", ROOT / "scripts/context_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_context_metrics_report_exact_comments_and_function_span(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("# one\ndef small():\n    return 1  # two\n")
    row = metrics.metrics(source)
    assert row["lines"] == 3
    assert row["comment_lines"] == 2
    assert row["longest_function"] == "small"
    assert row["longest_function_lines"] == 2
