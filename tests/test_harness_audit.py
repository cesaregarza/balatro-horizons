import importlib.util
import json

from test_boundary import project

from balatro_horizons.agents.protocol import context
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.service import ReviewService

spec = importlib.util.spec_from_file_location("audit_harness", ROOT / "scripts/audit_harness.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def test_request_reconstruction_is_public_scoped_and_does_not_send(store, monkeypatch):
    def never_send(*args, **kwargs):
        raise AssertionError("audit must not call provider")

    monkeypatch.setattr(DirectProvider, "send", never_send)
    cfg = load_config(ROOT / "configs/luna-smoke.yaml")
    eid = store.create(
        {"agent": "luna", "evidence_kind": "SYNTHETIC_TEST", "config": cfg.public()},
        {"seed": "PRIVATE_SEED_SENTINEL"},
    )
    other = store.create({"agent": "heuristic", "evidence_kind": "SYNTHETIC_TEST"})
    observation = project(FakeGame().observe_private())
    store.append(eid, "observation", observation.model_dump(mode="json"), observation_id=0)
    policy = DirectProvider(cfg.models["luna"], cfg.budgets)
    try:
        body = policy.request(context(observation), [])
    finally:
        policy.client.close()
    store.append(eid, "provider_request", {"body": body, "reserved_usd": 0.1}, observation_id=0)
    store.finish(eid, {"outcome": "GAME_LOSS"})
    before = store.summary(eid)["journal_head"]
    result = audit_module.audit(store, eid, compare=True)
    assert result["tools_v2_request_reconstruction"]["decisions"] == 1
    assert not result["tools_v2_request_reconstruction"]["failures"]
    assert "PRIVATE_SEED_SENTINEL" not in json.dumps(result)
    assert store.summary(eid)["journal_head"] == before
    assert ReviewService(store).exposure(eid)["outcome_seen"]
    assert not ReviewService(store).exposure(other)["records"]
