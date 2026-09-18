"""Offline reporting tests; these fixtures are not native game evidence."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_boundary import project

from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import Store

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summary_script = load_script("summarize_run")
renderer = load_script("decision_summary")


def test_latest_model_uses_manifest_time_and_exact_selected_model(tmp_path):
    store = Store(tmp_path / "data")
    config = {"models": {"selected": {"model": "sol"}, "unused": {"model": "terra"}}}
    older = store.create({"agent": "selected", "config": config, "created_at": "2026-01-01"})
    newer = store.create({"agent": "selected", "config": config, "created_at": "2026-01-02"})
    store.reindex(older)
    assert summary_script.latest_model_episode(store, "sol") == newer
    with pytest.raises(ValueError, match="NO_EPISODE_FOR_MODEL"):
        summary_script.latest_model_episode(store, "terra")


def test_status_uses_journal_not_stale_index_and_omits_payloads(tmp_path):
    store = Store(tmp_path / "data")
    eid = store.create({"agent": "fixture", "evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "provider_response", {"body": {"output": "opaque-secret"}})
    store.append(eid, "terminal", {
        "reason": "NATIVE_STARTUP_HANDSHAKE_TIMEOUT", "outcome": "INFRASTRUCTURE_FAILURE",
        "cost_usd": 0, "provider_calls": 0, "committed_actions": 0,
        "untrusted": "opaque-secret",
    })
    assert store.list_episodes()[0]["summary"] is None
    status = summary_script.run_status(store, eid)
    assert status["terminal"] and status["reason"] == "NATIVE_STARTUP_HANDSHAKE_TIMEOUT"
    assert status["provider_responses"] == 1 and status["action_commits"] == 0
    assert "opaque-secret" not in json.dumps(status)
    assert ReviewService(store).exposure(eid)["outcome_seen"]
    journal = store.episode_path(eid) / "events.jsonl"
    journal.write_text(journal.read_text().replace("NATIVE_STARTUP", "INVALID_STARTUP"))
    with pytest.raises(ValueError, match="JOURNAL_INTEGRITY_FAILURE"):
        summary_script.run_status(store, eid)


@pytest.fixture
def recorded_run(tmp_path):
    store = Store(tmp_path / "data")
    eid = store.create(
        {"agent": "fixture", "evidence_kind": "SYNTHETIC_TEST"},
        {"seed": "PRIVATE_SEED_SENTINEL"},
        eid="e" * 32,
    )
    game = FakeGame()
    game.phase = "SHOP"
    before = project(game.observe_private()).model_dump(mode="json")
    store.append(eid, "observation", before, observation_id=0)
    buy = {
        "observation_id": 0,
        "action": {"type": "buy", "offer_id": before["state"]["offers"][0]["id"]},
        "decision_note": "Buy scoring now.",
    }
    store.append(eid, "action_intent", buy, observation_id=0, request_id="buy")
    store.append(eid, "action_commit", buy, observation_id=0, request_id="buy")
    game.money = 0
    game.bought = True
    after = project(game.observe_private(), index=1).model_dump(mode="json")
    store.append(eid, "observation", after, observation_id=1)
    reorder = {
        "observation_id": 1,
        "action": {"type": "reorder", "area": "jokers", "ordered_ids": []},
        "decision_note": "Reorder request.",
    }
    store.append(eid, "action_intent", reorder, observation_id=1, request_id="reorder")
    store.append(
        eid,
        "action_rejected",
        {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"},
        observation_id=1,
        request_id="reorder",
    )
    return store, eid


def test_summary_preserves_completed_and_rejected_choices(recorded_run):
    store, eid = recorded_run
    store.finish(eid, {"committed_actions": 1, "outcome": "INVALID_EVALUATION"})
    public = summary_script.summary_input(store, eid)
    result = summary_script.summarize(public)
    assert result["ledger_action_count"] == 1
    assert result["actions"][0]["item"] == "Test Joker"
    assert result["actions"][0]["money_change"] == "-4"
    assert len(result["uncommitted_actions"]) == 1
    assert result["uncommitted_actions"][0]["decision"] == 1
    report = renderer.render_decisions(result)
    assert report == renderer.render_decisions(result)
    assert "Buy Test Joker" in report and "Cash $4 → $0" in report
    assert "Buy scoring now." in report and "Reorder request." in report
    assert "Requests without a committed transition" in report
    assert "NATIVE&#95;PUBLIC&#95;LEGALITY&#95;MISMATCH" in report
    exposure = ReviewService(store).exposure(eid)
    assert exposure["outcome_seen"] and exposure["model_identity_seen"]
    assert exposure["max_event_seen"] == len(store.events(eid)) - 1
    assert store.events(eid)[-1]["hash"] == public["journal_head"]


def test_provider_opaque_content_is_excluded_before_privacy_scan(recorded_run):
    store, eid = recorded_run
    store.append(
        eid,
        "provider_request",
        {
            "body": {
                "instructions": "PRIVATE_SEED_SENTINEL",
                "tools": [{"name": "buy", "description": "not needed"}],
            }
        },
    )
    store.append(
        eid,
        "provider_response",
        {
            "body": {
                "output": [{"encrypted_content": "PRIVATE_SEED_SENTINEL"}],
                "usage": {"input_tokens": 100, "output_tokens": 10, "extra": "not needed"},
            }
        },
    )
    result = summary_script.summary_input(store, eid)
    assert "PRIVATE_SEED_SENTINEL" not in json.dumps(result)
    assert "encrypted_content" not in json.dumps(result)
    assert result["events"][-1]["payload"] == {
        "body": {"usage": {"input_tokens": 100, "output_tokens": 10}}
    }
    assert result["events"][-2]["payload"] == {"body": {"tools": [{"name": "buy"}]}}


def test_public_notes_still_fail_closed_on_private_seed(recorded_run):
    store, eid = recorded_run
    store.append(
        eid,
        "action_intent",
        {
            "observation_id": 1,
            "action": {"type": "leave_shop"},
            "decision_note": "PRIVATE_SEED_SENTINEL",
        },
        observation_id=1,
        request_id="private-note",
    )
    with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
        summary_script.summary_input(store, eid)
    assert not ReviewService(store).exposure(eid)["records"]


def test_renderer_escapes_model_text_and_discloses_missing_notes(recorded_run):
    store, eid = recorded_run
    result = summary_script.summarize(summary_script.summary_input(store, eid))
    result["actions"][0]["note"] = "<script>alert(1)</script> | [link](javascript:alert(1)) `code`"
    result["uncommitted_actions"][0]["note"] = None
    report = renderer.render_decisions(result)
    assert "<script>" not in report and "[link]" not in report
    assert "&lt;script&gt;" in report and "&#124;" in report
    assert "&#91;link&#93;" in report and "&#96;code&#96;" in report
    assert "No note recorded." in report


def test_legacy_export_without_request_ids_remains_supported(recorded_run):
    store, eid = recorded_run
    public = summary_script.summary_input(store, eid)
    public["events"] = [e for e in public["events"] if e["type"] != "action_intent"]
    for event in public["events"]:
        event.pop("request_id", None)
    result = summary_script.summarize(public)
    assert result["ledger_action_count"] == 1
    assert result["uncommitted_actions"] == []


def test_cleared_round_keeps_the_target_that_was_played_against(tmp_path):
    store = Store(tmp_path / "data")
    eid = store.create({"agent": "fixture", "evidence_kind": "SYNTHETIC_TEST"}, eid="e" * 32)
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    before = project(game.observe_private()).model_dump(mode="json")
    store.append(eid, "observation", before, observation_id=0)
    store.append(
        eid,
        "action_commit",
        {
            "observation_id": 0,
            "action": {"type": "play_hand", "card_ids": [before["state"]["hand"][0]["id"]]},
        },
        observation_id=0,
        request_id="play",
    )
    game.phase = "ROUND_EVAL"
    after = project(game.observe_private(), index=1).model_dump(mode="json")
    after["state"]["resources"]["target"] = "0"
    store.append(eid, "observation", after, observation_id=1)
    result = summary_script.summarize(summary_script.summary_input(store, eid))
    report = renderer.render_decisions(result)
    assert "+12 chips; 12/10 total" in report
    assert "12/0 total" not in report


def test_status_distinguishes_saved_checkpoint_from_certified_restore(recorded_run):
    store, eid = recorded_run
    store.private_json(eid, "checkpoint-1.json", {
        "cost": 4.6, "calls": 146, "game": {"blob": "PRIVATE_SEED_SENTINEL"},
    })
    result = summary_script.run_status(store, eid)
    assert result["continuation"]["decision"] == 1
    assert result["continuation"]["checkpoint_saved"] is True
    assert result["continuation"]["restoration"] == "CHECKPOINT_NOT_CERTIFIED"
    assert result["continuation"]["protocol"] == "AGENT_PROTOCOL_SNAPSHOT_MISSING"
    assert result["continuation"]["checkpoint_cost"] == 4.6
    assert "PRIVATE_SEED_SENTINEL" not in json.dumps(result)
