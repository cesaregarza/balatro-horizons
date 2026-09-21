
"""Skill access uses public frozen text, the ordinary helper loop, and no network."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project
from test_harness_tools import config_for

from balatro_horizons.agents.baselines import Baseline, ScriptedPolicy
from balatro_horizons.agents.skills import load_guide, prepare_rules, read_guide, restore_knowledge
from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context, decision_context
from balatro_horizons.harness.contract import Operation
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.runner import Runner
from balatro_horizons.storage.journal import digest
from balatro_horizons.workbench.branches import prepare_branch
from balatro_horizons.workbench.service import WorkbenchService as ReviewService


def test_catalog_has_descriptions_without_loading_bodies_and_provider_parity():
    guide, skills, fingerprint = load_guide()
    assert len(skills) == 12 and len(guide["entries"]) == 24 and fingerprint
    observation = project(FakeGame().observe_private())
    ctx = context(observation, skills=skills)
    for item in skills:
        assert item["description"] in ctx["rules_kernel"]
        assert guide["entries"][item["key"]] not in ctx["rules_kernel"]
    requests = []
    for provider in ("openai", "anthropic"):
        config = config_for(provider)
        policy = DirectProvider(config.models["luna"], config.budgets)
        requests.append(policy.request(ctx, []))
        policy.client.close()
    assert requests[0]["input"][0]["content"][0]["text"] == requests[1]["system"]
    left = next(t["parameters"] for t in requests[0]["tools"] if t["name"] == "read_skill")
    right = next(t["input_schema"] for t in requests[1]["tools"] if t["name"] == "read_skill")
    assert left == right
    assert len(left["properties"]["name"]["enum"]) == 12
    followup, _ = decision_context(
        observation,
        [{"operation": {"kind": "arithmetic", "expression": "1+1"}, "result": {"result": "2"}}],
        skills=skills,
    )
    assert followup["skill_catalog_delivery"] == "names_and_descriptions"
    assert skills[0]["description"] in followup["rules_kernel"]


def test_skill_reads_are_frozen_public_and_paginated():
    rules = prepare_rules({"core": "base rule"}, "balatro-guide-v1")
    original = deepcopy(rules)
    body = ""
    key = "guide/balatro-consumables/catalog"
    while key:
        result = helper(Operation.validate_python({"kind": "rules", "key": key}), [], rules)
        assert len(result["entry"].encode()) <= 4096 and not result["game_advanced"]
        body += result["entry"]
        key = result["next_key"]
    assert body == rules["entries"]["guide/balatro-consumables/catalog"]
    assert rules == original
    assert (
        helper(Operation.validate_python({"kind": "skill", "name": "balatro-unknown"}), [], rules)[
            "error"
        ]
        == "UNKNOWN_SKILL"
    )
    with pytest.raises(ValueError):
        read_guide(rules, "guide/limits#offset=-1")
    with pytest.raises(ValueError):
        Operation.validate_python({"kind": "skill", "name": "../../private"})
    assert (
        helper(Operation.validate_python({"kind": "rules", "key": "../../private"}), [], rules)[
            "entry"
        ]
        is None
    )


def test_missing_or_tampered_guide_fails_before_provider_calls(tmp_path):
    from balatro_horizons.config import ROOT

    guide = json.loads((ROOT / "docs/balatro-guide/rules.json").read_text())
    guide["entries"]["guide/limits"] = "tampered"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(guide))
    with pytest.raises(ValueError, match="GUIDE_INTEGRITY_FAILURE"):
        load_guide(path)
    with pytest.raises(ValueError, match="GUIDE_RULES_COLLISION"):
        prepare_rules({"guide/limits": "collision"}, "balatro-guide-v1")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_model_reads_skill_and_reference_then_completes_synthetic_run(
    store, monkeypatch, provider
):
    config = config_for(provider)
    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "mock-only"
    )
    game = FakeGame()
    baseline = Baseline("heuristic")
    requests = []
    runner = None

    def receive(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            name, args = "read_skill", {"name": "balatro-scoring"}
        elif len(requests) == 2:
            assert runner.committed == 0 and "Estimate a candidate score" in json.dumps(body)
            name, args = "read_rules", {"key": "guide/balatro-scoring/mechanics"}
        else:
            if len(requests) == 3:
                assert runner.committed == 0 and "Scoring mechanics reference" in json.dumps(body)
            envelope = baseline.decide(context(runner.observation), [])["envelope"]
            action = dict(envelope["action"])
            name = action.pop("type")
            args = {
                **action,
                "observation_id": envelope["observation_id"],
                "decision_note": None,
                "note_update": {
                    "key": "guide",
                    "text": "Consult guide/balatro-scoring when needed.",
                },
            }
        if provider == "openai":
            payload = {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_{len(requests)}",
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            }
        else:
            payload = {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tool_{len(requests)}",
                        "name": name,
                        "input": args,
                    }
                ],
            }
        payload["usage"] = {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        }
        return httpx.Response(200, json=payload)

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    runner = Runner(store, config, game, policy)
    result = runner.run()
    assert result["outcome"] == "WIN"
    assert result["provider_calls"] == result["committed_actions"] + 2
    # Public helper results remain in bounded working memory; the note is durable too.
    assert "Estimate a candidate score" in json.dumps(requests[3])
    assert "Consult guide/balatro-scoring when needed." in json.dumps(requests[3])
    events = store.events(result["episode_id"])
    assert len([e for e in events if e["type"] == "helper_result"]) == 2
    started = next(e["payload"] for e in events if e["type"] == "episode_start")
    assert started["knowledge"]["preset"] == "balatro-guide-v1"
    frozen = json.loads(
        (store.episode_path(result["episode_id"], True) / "knowledge.json").read_text()
    )
    assert digest(frozen) == started["rules_hash"]
    review = ReviewService(store)
    opened = review.open(result["episode_id"])
    assert "helper_result" not in json.dumps(opened["view"])
    assert "helper_result" in json.dumps(review.advance(opened["review_token"]))


def test_branch_uses_parent_knowledge_and_rejects_tampering(store, config, monkeypatch):
    summary = Runner(store, config, FakeGame(), Baseline("heuristic")).run()
    eid = summary["episode_id"]
    checkpoint = json.loads((store.episode_path(eid, True) / "checkpoint-0.json").read_text())
    expected = restore_knowledge(store, checkpoint)
    verify_checkpoint(store, config, eid, 0, repetitions=3)
    bid, checkpoint, prefix = prepare_branch(store, config, eid, 0, "agent_continue")
    monkeypatch.setattr(
        "balatro_horizons.agents.skills.load_guide",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("live guide must not be read on resume")
        ),
    )
    result = Runner(store, Config(skills="none"), FakeGame(), Baseline("heuristic")).run(
        eid=bid, resume=checkpoint, history_prefix=prefix
    )
    assert result["outcome"] == "WIN"
    actual = json.loads((store.episode_path(bid, True) / "knowledge.json").read_text())
    assert actual == expected
    parent_path = store.episode_path(eid, True) / "knowledge.json"
    parent_path.write_text(json.dumps({"entries": {"evil": "changed"}}))
    with pytest.raises(ValueError, match="KNOWLEDGE_SNAPSHOT_MISMATCH"):
        restore_knowledge(store, checkpoint)
    with pytest.raises(ValueError, match="KNOWLEDGE_SNAPSHOT_MISSING"):
        restore_knowledge(store, {})


def test_opt_out_and_skill_helper_budget_do_not_advance_game(store):
    cfg = Config(skills="none")
    result = Runner(store, cfg, FakeGame(), Baseline("heuristic")).run()
    ctx = next(
        e["payload"]["context"]
        for e in store.events(result["episode_id"])
        if e["type"] == "agent_context"
    )
    assert "Available Balatro skills" not in ctx["rules_kernel"]
    cfg = Config()
    cfg.budgets.max_helper_calls_per_decision = 1
    read = {"kind": "skill", "name": "balatro-scoring"}
    game = FakeGame()
    result = Runner(store, cfg, game, ScriptedPolicy([read] * 4)).run()
    assert result["reason"] == "AGENT_PROTOCOL_FAILURE" and result["committed_actions"] == 0
    assert sum(
        event["type"] == "helper_result" for event in store.events(result["episode_id"])
    ) == 1


def test_current_discovery_and_missing_snapshot(store):
    _, skills, _ = load_guide()
    observation = project(FakeGame().observe_private())
    ctx, _ = decision_context(
        observation,
        [{"operation": {"kind": "arithmetic", "expression": "1+1"}, "result": {"result": "2"}}],
        skills=skills,
    )
    assert "read_skill(" in ctx["rules_kernel"]
    assert all(s["name"] in ctx["rules_kernel"] for s in skills)
    assert ctx["skill_catalog_delivery"] == "names_and_descriptions"
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST"}, {})
    with pytest.raises(ValueError, match="KNOWLEDGE_SNAPSHOT_MISSING"):
        restore_knowledge(store, {"knowledge": {"episode_id": eid, "hash": "absent"}})


def test_skill_settings_roundtrip_preserves_omitted_choice(store, config):
    from fastapi.testclient import TestClient

    from balatro_horizons.api import create_app

    with TestClient(create_app(store.root, config)) as client:
        boot = client.get("/api/bootstrap").json()
        assert boot["config"]["skills"] == "balatro-guide-v1"
        headers = {"X-BH-Operator": boot["operator_token"]}
        setting = {
            "models": config.models,
            "budgets": config.budgets.model_dump(),
            "skills": "none",
        }
        assert client.put("/api/settings", headers=headers, json=setting).json()["skills"] == "none"
        del setting["skills"]
        assert client.put("/api/settings", headers=headers, json=setting).json()["skills"] == "none"
    with TestClient(create_app(store.root, config)) as client:
        assert client.get("/api/bootstrap").json()["config"]["skills"] == "none"


def test_branch_capability_requires_knowledge_snapshot(store, workbench_config, episode):
    from fastapi.testclient import TestClient

    from balatro_horizons.api import create_app

    config = workbench_config
    verify_checkpoint(store, config, episode, 0, repetitions=3)
    with TestClient(create_app(store.root, config)) as client:
        op = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        opened = client.post("/api/reviews", headers=op, json={"episode_id": episode}).json()
        headers = {"X-Review-Token": opened["review_token"]}
        assert client.get("/api/review/branch-capability", headers=headers).json()["enabled"]
        (store.episode_path(episode, True) / "knowledge.json").unlink()
        assert not client.get("/api/review/branch-capability", headers=headers).json()["enabled"]


def test_native_calibration_probe_completes_offline_before_native_use(store, config):
    import runpy

    from balatro_horizons.config import ROOT

    policy = runpy.run_path(str(ROOT / "scripts/native_runs.py"))["SkillReadBaseline"]()
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["outcome"] == "WIN" and result["provider_calls"] == 0
    helpers = [e for e in store.events(result["episode_id"]) if e["type"] == "helper_result"]
    assert len(helpers) == 2
    assert all(e["observation_id"] == 0 for e in helpers)
    assert all(e["payload"]["result"].get("reference") == "balatro_guide" for e in helpers)
