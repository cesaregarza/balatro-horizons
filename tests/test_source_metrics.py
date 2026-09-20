"""The source-size report fails closed and measures real Python syntax."""

import importlib.util

import pytest

from balatro_horizons.config import ROOT

SPEC = importlib.util.spec_from_file_location("source_metrics", ROOT / "scripts/source_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_measure_counts_comments_and_nested_functions(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("# top\ndef outer():\n    # inside\n    def inner():\n        return 1\n")
    row = metrics.measure(source)
    assert row["lines"] == 5
    assert row["comment_count"] == 2
    assert row["longest_function"] == {"name": "outer", "lines": 4}


def test_invalid_input_and_limit_fail(tmp_path, capsys):
    with pytest.raises(ValueError, match="INVALID_SOURCE_PATH"):
        metrics.python_files([tmp_path / "missing.py"])
    source = tmp_path / "sample.py"
    source.write_text("def example():\n    return 1\n")
    assert metrics.main([str(source), "--max-function-lines", "1"]) == 1
    assert '"violations"' in capsys.readouterr().out
    assert metrics.main([str(source), "--max-function-lines", "1",
                         "--frozen-function-exception", str(source)]) == 0
    assert '"frozen_function_exceptions"' in capsys.readouterr().out
    with pytest.raises(SystemExit, match="2"):
        metrics.main([str(source), "--frozen-function-exception", str(tmp_path / "absent.py")])
