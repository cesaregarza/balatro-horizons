"""Offline reporting tests; these fixtures are not native game evidence."""

import json

import pytest
from test_boundary import project

from balatro_horizons.cli import main as cli_main
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.review.decision_ledger import summarize, summary_input
from balatro_horizons.review.run_status import run_status
from balatro_horizons.review.service import ReviewService
from balatro_horizons.review.summary import render_decisions
from balatro_horizons.storage.journal import Store, atomic_json, digest


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


def test_summarize_command_writes_package_outputs(recorded_run, tmp_path, capsys):
    store, eid = recorded_run
    store.finish(eid, {"committed_actions": 1, "outcome": "INVALID_EVALUATION"})
    output = tmp_path / "summary.json"
    markdown = tmp_path / "summary.md"

    assert cli_main([
        "--data-dir", str(store.root), "summarize",
        "--episode-id", eid, "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text())
    assert payload["ledger_action_count"] == 1
    assert "Buy Test Joker" in markdown.read_text()
    assert json.loads(capsys.readouterr().out)["recorded_actions"] == 1


def test_summarize_help_preserves_operator_contract(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli_main(["summarize", "--help"])
    assert stopped.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "No game or provider is contacted." in help_text
    assert "Reading a whole run records review exposure." in help_text
    assert "Also write an ante-grouped decision recap" in help_text


def test_summary_preserves_completed_and_rejected_choices(recorded_run):
    store, eid = recorded_run
    store.finish(eid, {"committed_actions": 1, "outcome": "INVALID_EVALUATION"})
    public = summary_input(store, eid)
    result = summarize(public)
    assert result["ledger_action_count"] == 1
    assert result["actions"][0]["item"] == "Test Joker"
    assert result["actions"][0]["money_change"] == "-4"
    assert len(result["uncommitted_actions"]) == 1
    assert result["uncommitted_actions"][0]["decision"] == 1
    report = render_decisions(result)
    assert report == render_decisions(result)
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
    result = summary_input(store, eid)
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
        summary_input(store, eid)
    assert not ReviewService(store).exposure(eid)["records"]


def test_renderer_escapes_model_text_and_discloses_missing_notes(recorded_run):
    store, eid = recorded_run
    result = summarize(summary_input(store, eid))
    result["actions"][0]["note"] = "<script>alert(1)</script> | [link](javascript:alert(1)) `code`"
    result["uncommitted_actions"][0]["note"] = None
    report = render_decisions(result)
    assert "<script>" not in report and "[link]" not in report
    assert "&lt;script&gt;" in report and "&#124;" in report
    assert "&#91;link&#93;" in report and "&#96;code&#96;" in report
    assert "No note recorded." in report


def test_legacy_export_without_request_ids_remains_supported(recorded_run):
    store, eid = recorded_run
    public = summary_input(store, eid)
    public["events"] = [e for e in public["events"] if e["type"] != "action_intent"]
    for event in public["events"]:
        event.pop("request_id", None)
    result = summarize(public)
    assert result["ledger_action_count"] == 1
    assert result["uncommitted_actions"] == []


def test_historical_harness_label_comes_only_from_recorded_bundle(recorded_run):
    store, eid = recorded_run
    bundle = {"version": "agent-protocol-v1", "interface": "retired-recording"}
    path = store.episode_path(eid, True) / "agent-protocol.json"
    atomic_json(path, bundle)
    store.append(eid, "episode_start", {"agent_protocol": {"episode_id": eid, "hash": digest(bundle)}})
    manifest = summary_input(store, eid)["manifest"]
    assert manifest["recorded_interface"] == "retired-recording"
    assert manifest["current_harness"] is False
    atomic_json(path, {**bundle, "interface": "tampered"})
    assert summary_input(store, eid)["manifest"]["recorded_interface"] is None


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
    result = summarize(summary_input(store, eid))
    report = render_decisions(result)
    assert "+12 chips; 12/10 total" in report
    assert "12/0 total" not in report


def test_status_distinguishes_saved_checkpoint_from_certified_restore(recorded_run):
    store, eid = recorded_run
    store.private_json(eid, "checkpoint-1.json", {
        "cost": 4.6, "calls": 146, "game": {"blob": "PRIVATE_SEED_SENTINEL"},
    })
    result = run_status(store, eid)
    assert result["continuation"]["decision"] == 1
    assert result["continuation"]["checkpoint_saved"] is True
    assert result["continuation"]["restoration"] == "CHECKPOINT_NOT_CERTIFIED"
    assert result["continuation"]["protocol"] == "AGENT_PROTOCOL_SNAPSHOT_MISSING"
    assert result["continuation"]["checkpoint_cost"] == 4.6
    assert "PRIVATE_SEED_SENTINEL" not in json.dumps(result)
