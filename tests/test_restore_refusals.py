"""Lost games and corrupt recovery inputs fail before creating another attempt."""

import json

import pytest
import test_campaign_budget
from test_restore_unfinished import finish, interrupted, request_for

from balatro_horizons import service as service_module
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.workbench.budget_ledger import validate_spending_entries

harness = test_campaign_budget.harness


def terminal_game(monkeypatch, outcome):
    class TerminalGame(service_module.FakeGame):
        def terminal_status(self):
            return outcome if super().terminal_status() else None
    monkeypatch.setattr(service_module, "FakeGame", TerminalGame)


def assert_refused(h, parent, reason, plan=None):
    before = len(h.games), len(h.calls), len(h.store.list_episodes())
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == reason
    with pytest.raises(ValueError, match=reason):
        start_restore(h.service(), parent, request_for(plan or {
            "parent_head": "0" * 64, "plan_hash": "0" * 64,
        }), paid_enabled=True)
    assert (len(h.games), len(h.calls), len(h.store.list_episodes())) == before


@pytest.mark.parametrize("outcome", ["WIN", "GAME_LOSS"])
def test_finished_root_cannot_restore(harness, monkeypatch, outcome):
    terminal_game(monkeypatch, outcome)
    result = harness.service().execute(harness.config, "luna", "PRIVATE_DONE", offline=True)
    assert result["outcome"] == outcome
    assert_refused(harness, result["episode_id"], "RESTORE_RUN_FINISHED")


@pytest.mark.parametrize("outcome", ["WIN", "GAME_LOSS"])
def test_finished_restore_child_closes_the_entire_restore_family(harness, monkeypatch, outcome):
    h = harness
    parent = interrupted(h)
    plan = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    terminal_game(monkeypatch, outcome)
    service = h.service()
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    assert h.store.summary(child)["outcome"] == outcome
    assert_refused(h, child, "RESTORE_RUN_FINISHED")
    assert_refused(h, parent, "RESTORE_ALREADY_FINISHED", plan)


@pytest.mark.parametrize("damage,reason", [
    ("journal", "JOURNAL_INTEGRITY_FAILURE"),
    ("raw", "CHECKPOINT_CONTINUATION_MISMATCH"),
])
def test_corrupt_restore_evidence_refuses_preview_and_post(harness, damage, reason):
    h = harness
    parent = interrupted(h)
    plan = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    if damage == "raw":
        (h.store.episode_path(parent, True) / "raw-0.json").unlink()
    else:
        path = h.store.episode_path(parent) / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[0]["hash"] = "f" * 64
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert_refused(h, parent, reason, plan)


@pytest.mark.parametrize("row", [
    None, {"episode_id": []}, {"episode_id": "wrong", "settled": False, "cost": 0},
    {"episode_id": "root", "settled": 1, "cost": 0},
    {"episode_id": "root", "settled": False, "cost": True},
    {"episode_id": "root", "settled": False, "cost": float("nan")},
    {"episode_id": "root", "settled": False, "cost": -1},
])
def test_shared_spending_row_validation(row):
    with pytest.raises(ValueError, match="RESTORE_LEDGER_INVALID"):
        validate_spending_entries({"request": row}, owners={"root"}, allow_empty=True,
                                  error="RESTORE_LEDGER_INVALID")


def test_shared_spending_validation_preserves_different_empty_ledger_policies():
    validate_spending_entries({}, owners={"root"}, allow_empty=True)
    with pytest.raises(ValueError, match="BUDGET_EXTENSION_INVALID_LEDGER"):
        validate_spending_entries({})
