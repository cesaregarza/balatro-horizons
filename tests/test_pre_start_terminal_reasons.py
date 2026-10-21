"""Pre-start execution failures end with a named code, never an exception class or message."""

import json
from unittest.mock import Mock

import pytest

from balatro_horizons import service
from balatro_horizons.evaluation.reports import episode_export


class _UnnamedPluginError(Exception):
    pass


def _fail_before_start(store, config, monkeypatch, error):
    runs = service.RunService(store, Mock())
    monkeypatch.setattr(runs, "create_game", Mock(side_effect=error))
    with pytest.raises(type(error)):
        runs.execute(config, "heuristic", "MOCK", offline=True)
    (row,) = store.list_episodes()
    return row["episode_id"], row["summary"]


@pytest.mark.parametrize(
    "error",
    [
        OSError("/home/operator/private/balatro.lock"),
        ValueError("PRIVATE STATE AT /TMP/SAVE"),
        ValueError("A" * 81),
        ValueError("lowercase message"),
        _UnnamedPluginError(),
    ],
)
def test_unnamed_pre_start_failure_is_harness_unexpected_error(store, config, monkeypatch, error):
    eid, summary = _fail_before_start(store, config, monkeypatch, error)
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["reason"] == "HARNESS_UNEXPECTED_ERROR"
    diagnostic = json.loads((store.episode_path(eid, True) / "failure-diagnostic.json").read_text())
    assert diagnostic["exception_type"] == type(error).__name__
    exported = json.dumps(episode_export(store, eid))
    assert type(error).__name__ not in exported
    assert not str(error) or str(error) not in exported


def test_named_pre_start_code_is_kept(store, config, monkeypatch):
    _, summary = _fail_before_start(
        store, config, monkeypatch, ValueError("FROZEN_RULES_ENVIRONMENT_MISMATCH")
    )
    assert summary["reason"] == "FROZEN_RULES_ENVIRONMENT_MISMATCH"
