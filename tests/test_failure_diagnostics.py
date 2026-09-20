import json

import pytest

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.runner import Runner


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

    result = Runner(store, config, FakeGame(), FailingPolicy("heuristic")).run()
    assert result["reason"] == ("LOCAL_CONTEXT_LIMIT" if known else "ValueError")
    eid = result["episode_id"]
    diagnostic = json.loads((store.episode_path(eid, True) / "failure-diagnostic.json").read_text())
    assert any(f["function"] == "decide" for f in diagnostic["frames"])
    assert "PRIVATE_SENTINEL" not in json.dumps(diagnostic)
    exported = episode_export(store, eid)
    assert "PRIVATE_SENTINEL" not in json.dumps(exported)
    assert "failure-diagnostic.json" not in json.dumps(exported)
    if known:
        event = next(e for e in store.events(eid) if e["type"] == "harness_failure")
        assert event["payload"]["stage"] == "helper_followup"
        assert event["payload"]["request_bytes"] == 35000
