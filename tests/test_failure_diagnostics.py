import json

import pytest

from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending


@pytest.mark.parametrize("known", [True, False])
def test_error_code_and_private_stack_without_secret_text(store, config, known):
    class FailingPolicy(Baseline):
        def decide(self, *args):
            if known:
                raise HarnessFailure(
                    "LOCAL_CONTEXT_LIMIT",
                    stage="helper_followup",
                    request_bytes=35000,
                    byte_limit=32768,
                )
            raise ValueError("PRIVATE_SENTINEL is an exception message, not an error code")

    result = Runner(
        store,
        config,
        FakeGame(),
        FailingPolicy("heuristic"),
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run()
    assert result["reason"] == ("LOCAL_CONTEXT_LIMIT" if known else "HARNESS_UNEXPECTED_ERROR")
    eid = result["episode_id"]
    diagnostic = json.loads((store.episode_path(eid, True) / "failure-diagnostic.json").read_text())
    assert diagnostic["exception_type"] == ("HarnessFailure" if known else "ValueError")
    assert any(f["function"] == "decide" for f in diagnostic["frames"])
    assert "PRIVATE_SENTINEL" not in json.dumps(diagnostic)
    exported = episode_export(store, eid)
    assert "PRIVATE_SENTINEL" not in json.dumps(exported)
    assert "failure-diagnostic.json" not in json.dumps(exported)
    if known:
        event = next(e for e in store.events(eid) if e["type"] == "harness_failure")
        assert event["payload"]["stage"] == "helper_followup"
        assert event["payload"]["request_bytes"] == 35000


class _UnnamedPluginError(Exception):
    pass


@pytest.mark.parametrize(
    "error",
    [
        ValueError("lowercase message"),
        ValueError("UPPERCASE_LOOKING_MESSAGE"),
        KeyError("hand_levels"),
        TypeError("bad operand"),
        RuntimeError(),
        _UnnamedPluginError("private detail"),
    ],
)
def test_no_unnamed_exception_class_reaches_terminal_reason(store, config, error):
    class FailingPolicy(Baseline):
        def decide(self, *args):
            raise error

    result = Runner(
        store,
        config,
        FakeGame(),
        FailingPolicy("heuristic"),
        Spending.episode_only(store.root / "private_runs" / "test-spending.json", 1),
    ).run()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["reason"] == "HARNESS_UNEXPECTED_ERROR"
    eid = result["episode_id"]
    terminal = next(e for e in store.events(eid) if e["type"] == "terminal")
    assert terminal["payload"]["reason"] == "HARNESS_UNEXPECTED_ERROR"
    # The exception class is retained only in the private failure record.
    diagnostic = json.loads((store.episode_path(eid, True) / "failure-diagnostic.json").read_text())
    assert diagnostic["exception_type"] == type(error).__name__
    assert type(error).__name__ not in json.dumps(episode_export(store, eid))
