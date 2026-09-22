"""Synthetic campaign/episode funding properties; no native or paid calls."""

import json
import math
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from provider_transport import with_input_count
from test_openai_luna import luna, response

from balatro_horizons import service as service_module
from balatro_horizons.evaluation.batches import plan_batch
from balatro_horizons.evaluation.reports import export_batch, report_batch, scan
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import (
    REFUSAL_OUTCOMES,
    BudgetExhausted,
    SchedulingStop,
    Spending,
    batch_attempts,
    read_stop,
    record_stop,
    reservation_usd,
)
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store


@pytest.fixture
def harness(store, monkeypatch):
    config = luna()
    config.budgets.paid_calls_enabled = True
    config.budgets.max_episode_cost_usd = 1
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    games, calls, clients, failures, operations, ledger_rows = [], [], [], [], [], []
    class CountedGame(FakeGame):
        def __init__(self, *args, **kwargs):
            games.append(self)
            super().__init__(*args, **kwargs)
    def receive(request):
        body = json.loads(request.content)
        spending_paths = sorted(store.root.glob("batches/*/spending.json"))
        ledger_rows.append(
            json.loads(spending_paths[-1].read_text()) if spending_paths else {}
        )
        calls.append(body)
        if failures:
            raise httpx.ReadTimeout("synthetic transport failure")
        message = next(item for item in body["input"] if item.get("role") == "user")
        context = json.loads(message["content"])
        operation = operations[0] if operations else Baseline("heuristic").decide(context, [])
        return httpx.Response(200, json=response(operation))
    def policy(model, limits):
        client = httpx.Client(transport=httpx.MockTransport(with_input_count(receive)))
        clients.append(client)
        return DirectProvider(model, limits, client=client)
    native = Mock(side_effect=AssertionError("native game must never launch"))
    monkeypatch.setattr(service_module, "FakeGame", CountedGame)
    monkeypatch.setattr(service_module, "NativeGame", native)
    monkeypatch.setattr(service_module, "DirectProvider", policy)
    def plan(count=2, agents=None):
        return plan_batch(store, config, {"seeds": [f"BUDGETFIXTURE{i}" for i in range(count)]}, agents or ["luna"], replicates=1)
    yield SimpleNamespace(
        config=config, store=store, games=games, calls=calls, clients=clients,
        failures=failures, operations=operations, ledger_rows=ledger_rows, native=native,
        service=lambda: RunService(store, ReviewService(store)), plan=plan,
    )
    for client in clients:
        client.close()
def assert_no_activity(h, plan=None):
    assert not h.games and not h.calls and not h.clients and h.store.list_episodes() == []
    if plan:
        assert read_stop(h.store, plan) is None
    h.native.assert_not_called()
@pytest.mark.parametrize(
    "episode_cap,campaign_cap,reason,game_count",
    [
        (0.0186384, 0.0186384, "EPISODE_AND_CAMPAIGN_COST_CAP", 1),
        (0.0186384, 0.04, "EPISODE_COST_CAP", 2),
        (1, 0.0200384, "CAMPAIGN_COST_CAP", 1),
        (1, 0.005, "CAMPAIGN_COST_CAP", 0),
    ],
)
def test_two_dimensional_caps_choose_refusal_and_game_admission(harness, episode_cap, campaign_cap, reason, game_count):
    h = harness
    h.config.budgets.max_episode_cost_usd = episode_cap
    h.config.budgets.max_batch_cost_usd = campaign_cap
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert REFUSAL_OUTCOMES[reason]["outcome"] in {"BUDGET_EXHAUSTED", "CAMPAIGN_INTERRUPTED"}
    assert len(h.games) == game_count
    if game_count == 0:
        assert read_stop(h.store, plan)["reason"] == reason
        assert not h.games and not h.calls and not h.clients
        h.native.assert_not_called()
    elif reason == "EPISODE_COST_CAP":
        assert read_stop(h.store, plan) is None
    else:
        assert read_stop(h.store, plan)["reason"] == reason
def test_preflight_stop_is_unresolved_and_write_once(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0200384
    plan = h.plan()
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    row = report_batch(h.store, bid, tmp_path / "report")["agents"]["luna"]
    assert (row["attempts"], row["valid"], row["wins"], row["unresolved"]) == (1, 1, 1, 1)
    assert row["win_rate"] == 1 and row["coverage"] == 0.5
    assert row["missing_outcome_bounds"] == [0.5, 1]
    path = h.store.root / "batches" / bid / "stop.json"
    before = path.read_bytes()
    h.service().run_batch(h.config, bid, offline=True)
    assert path.read_bytes() == before and len(h.games) == 1 and len(h.calls) == 5
def test_campaign_interrupt_and_episode_only_refusals_remain_distinct(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0208384
    plan = h.plan(3)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    rows = batch_attempts(h.store, plan)
    assert rows[1]["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert rows[1]["summary"]["committed_actions"] == 2
    assert rows[1]["terminal"]["payload"]["cost_usd"] == pytest.approx(0.00088)
    assert rows[1]["summary"]["cost_usd"] == pytest.approx(0.00088)
    stop = read_stop(h.store, plan)
    assert stop["stage"] == "episode"
    assert stop["terminal"]["hash"] == rows[1]["terminal"]["hash"]
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    assert report["agents"]["luna"]["unresolved"] == 2
    assert report["agents"]["luna"]["all_attempt_cost_usd"] == pytest.approx(0.00308)
    assert len(h.games) == 2 and len(h.calls) == 7 and len(h.clients) == 2


def test_episode_only_refusals_resolve_slots_without_stopping_batch(harness, tmp_path):
    h = harness
    h.config.budgets.max_episode_cost_usd = 0.0186384
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    rows = batch_attempts(h.store, plan)
    assert rows and all(row["summary"]["reason"] == "EPISODE_COST_CAP" for row in rows)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    assert report["agents"]["luna"]["unresolved"] == 0
    assert report["scheduling_stop"] is None
def test_unknown_usage_retains_reservations_and_next_model_controls_preflight(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.04
    h.config.budgets.max_transport_attempts = 1
    h.failures.append(True)
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")["agents"]["luna"]
    assert report["attempt_outcomes"] == {"INFRASTRUCTURE_FAILURE": 2}
    assert report["all_attempt_cost_usd"] == pytest.approx(0.0360448)
    stop = read_stop(h.store, plan)
    assert stop["stage"] == "preflight"
    assert stop["cost_context"]["unsettled_usd"] == pytest.approx(0.0360448)
    assert len(h.games) == 2 and len(h.calls) == 2 and len(h.clients) == 2


def test_next_model_controls_preflight_and_cheaper_model_cannot_resume(harness):
    h = harness
    h.config.models["pricey"] = h.config.models["luna"].model_copy(
        update={"input_usd_per_million": 20.0, "output_usd_per_million": 120.0}
    )
    h.config.budgets.max_episode_cost_usd = 2
    h.config.budgets.max_batch_cost_usd = 1.70
    plan = h.plan(1, ["pricey", "luna", "heuristic"])
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    (attempt,) = batch_attempts(h.store, plan)
    assert attempt["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert attempt["summary"]["cost_usd"] == pytest.approx(0.088)
    before = (h.store.root / "batches" / plan["batch_id"] / "stop.json").read_bytes()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert (h.store.root / "batches" / plan["batch_id"] / "stop.json").read_bytes() == before
    assert len(h.calls) == 2 and len(h.games) == 1


def test_next_model_determines_preflight_and_cannot_be_skipped(harness):
    h = harness
    h.config.models["pricey"] = h.config.models["luna"].model_copy(
        update={"input_usd_per_million": 20.0, "output_usd_per_million": 120.0}
    )
    h.config.budgets.max_episode_cost_usd = 2
    h.config.budgets.max_batch_cost_usd = 0.02
    plan = h.plan(1, ["luna", "pricey", "heuristic"])
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    stop = read_stop(h.store, plan)
    assert stop["agent"] == "pricey" and stop["stage"] == "preflight"
    assert stop["cost_context"]["required_usd"] == 1.6384
    assert len(h.calls) == 5 and len(h.games) == 1 and len(h.clients) == 1
def test_authoritative_reserve_and_overage_are_visible(tmp_path):
    spending = Spending(tmp_path / "ledger.json", 0.02)
    spending.reserve("old", "previous", 0.01496, 1)
    before = spending.path.read_bytes()
    accepted, context = spending.affordability(0.016384)
    assert spending.path.read_bytes() == before and context["unsettled_usd"] == 0.01496
    assert accepted == (0.01496 + 0.016384 <= 0.02)
    spending.settle("old", 0.03)
    affordable, context = spending.affordability(0.01)
    assert not affordable and context["campaign_committed_usd"] == 0.03
    assert context["campaign_headroom_usd"] == pytest.approx(-0.01)
    branch = Spending(tmp_path / "branch.json", 1)
    with pytest.raises(BudgetExhausted, match="^EPISODE_COST_CAP$"):
        branch.reserve("branch", "child", 0.016384, 0.017, prior_cost=0.00088)


@pytest.mark.parametrize("index_state", ["stale", "missing"])
def test_T08_recover_committed_terminal_before_index_or_stop_write(
    harness,
    monkeypatch,
    index_state,
    tmp_path,
):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0186384
    plan = h.plan()
    bid = plan["batch_id"]

    class SimulatedCrash(BaseException):
        pass

    def crash_after_terminal(eid, summary):
        h.store.append(eid, "terminal", summary)
        if index_state == "missing":
            with sqlite3.connect(h.store.db) as db:
                db.execute("DELETE FROM episodes")
        raise SimulatedCrash

    monkeypatch.setattr(h.store, "finish", crash_after_terminal)
    with pytest.raises(SimulatedCrash):
        h.service().run_batch(h.config, bid, offline=True)
    assert not (h.store.root / "batches" / bid / "stop.json").exists()
    indexed = h.store.list_episodes()
    assert indexed == [] if index_state == "missing" else indexed[0]["summary"] is None
    fresh = Store(h.store.root)
    RunService(fresh, ReviewService(fresh)).run_batch(h.config, bid, offline=True)
    stop = read_stop(fresh, plan)
    assert stop["recovered"] and stop["reason"] == "CAMPAIGN_COST_CAP"
    assert stop["recorded_at"] > stop["terminal"]["timestamp"]
    assert len(h.calls) == 2 and len(h.games) == 1
    report = report_batch(fresh, bid, tmp_path / "report")
    assert report["agents"]["luna"]["attempts"] == 1
    assert report["agents"]["luna"]["all_attempt_cost_usd"] == pytest.approx(0.00088)
    export_batch(fresh, bid, tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert len(bundle["episodes"]) == 1 and bundle["report"] == report


@pytest.mark.parametrize(
    "cap", [math.nextafter(0.031344, -math.inf), 0.031344, math.nextafter(0.031344, math.inf)]
)
def test_T09_preflight_and_reservation_share_exact_float_boundary(tmp_path, cap):
    spending = Spending(tmp_path / "ledger.json", cap)
    spending.reserve("old", "previous", 0.01496, 1)
    before = spending.path.read_bytes()
    accepted, context = spending.affordability(0.016384)
    assert spending.path.read_bytes() == before  # No reservation or settlement.
    assert context["unsettled_usd"] == 0.01496
    assert accepted == (0.01496 + 0.016384 <= cap)
    if accepted:
        spending.reserve("new", "next", 0.016384, 1)
    else:
        with pytest.raises(BudgetExhausted, match="^CAMPAIGN_COST_CAP$"):
            spending.reserve("new", "next", 0.016384, 1)
        assert spending.path.read_bytes() == before
@pytest.mark.parametrize("entrypoint", ["batch", "execute", "start"])
@pytest.mark.parametrize(
    "episode_cap,campaign_cap,reason",
    [
        (0.010, 0.005, "EPISODE_CAP_BELOW_RESERVATION"),
        (None, 0.005, "PAID_EXECUTION_NOT_AUTHORIZED"),
        (1, None, "PAID_EXECUTION_NOT_AUTHORIZED"),
    ],
)
def test_invalid_paid_caps_reject_before_episode_or_game(harness, entrypoint, episode_cap, campaign_cap, reason):
    h = harness
    h.config.budgets.max_episode_cost_usd = episode_cap
    h.config.budgets.max_batch_cost_usd = campaign_cap
    plan = h.plan()
    with pytest.raises(ValueError, match=f"^{reason}$"):
        if entrypoint == "batch":
            h.service().run_batch(h.config, plan["batch_id"], offline=True)
        elif entrypoint == "execute":
            h.service().execute(h.config, "luna", "fixture", offline=True)
        else:
            h.service().start(h.config, "luna", "fixture", offline=True)
    assert_no_activity(h, plan)
def test_reports_exports_and_legacy_records(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0208384
    plan = h.plan(3)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report_batch(h.store, plan["batch_id"], tmp_path / "report")
    export_batch(h.store, plan["batch_id"], tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    report = json.loads((tmp_path / "report/report.json").read_text())
    assert bundle["report"] == report and report["agents"]["luna"]["unresolved"] == 2
    assert sum(e["summary"]["cost_usd"] for e in bundle["episodes"]) == pytest.approx(
        report["agents"]["luna"]["all_attempt_cost_usd"]
    )
    scan(bundle, ["BUDGETFIXTURE1"])
    with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
        scan({**bundle, "injected": "BUDGETFIXTURE1"}, ["BUDGETFIXTURE1"])
    assert all(episode["summary"]["cost_usd"] == pytest.approx(0.0022 if episode["summary"]["outcome"] == "WIN" else 0.00088)
               for episode in bundle["episodes"])
    assert all(episode["manifest"]["evaluation_eligible"] for episode in bundle["episodes"])


def test_legacy_generic_cost_stop_is_not_reinterpreted(harness, tmp_path):
    h = harness
    plan = h.plan(1)
    slot = plan["slots"][0]
    eid = h.store.create({**slot, "batch_id": plan["batch_id"], "evidence_kind": "SYNTHETIC_TEST",
                          "evaluation_eligible": True, "config": h.config.public()},
                         {"seed": "BUDGETFIXTURE0"})
    h.store.finish(eid, {"outcome": "BUDGET_EXHAUSTED", "reason": "COST_CAP_REACHED", "cost_usd": 0.12})
    before = (h.store.episode_path(eid) / "events.jsonl").read_bytes()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "legacy")
    export_batch(h.store, plan["batch_id"], tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert bundle["report"] == report and report["scheduling_stop"] is None
    assert report["agents"]["luna"]["valid"] == 1
    assert h.store.summary(eid)["reason"] == "COST_CAP_REACHED"
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
    assert not h.games and not h.calls
def test_stop_recovery_and_first_writer_are_durable(harness, monkeypatch):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0186384
    plan = h.plan()
    bid = plan["batch_id"]
    class Crash(BaseException):
        pass
    def finish(eid, summary):
        h.store.append(eid, "terminal", summary)
        raise Crash
    monkeypatch.setattr(h.store, "finish", finish)
    with pytest.raises(Crash):
        h.service().run_batch(h.config, bid, offline=True)
    fresh = Store(h.store.root)
    RunService(fresh, ReviewService(fresh)).run_batch(h.config, bid, offline=True)
    assert read_stop(fresh, plan)["recovered"]
    _, context = Spending(h.store.root / "ledger.json", 0.005).affordability(0.016384)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(
            lambda slot: record_stop(h.store, plan, reason="CAMPAIGN_COST_CAP", stage="preflight",
                                     slot_id=slot["slot_id"], agent=slot["agent"], cost_context=context),
            plan["slots"],
        ))
    assert values[0] == values[1] == read_stop(h.store, plan)
def test_reservation_lock_is_process_scoped(tmp_path):
    path = tmp_path / "ledger.json"
    lock = path.with_suffix(".lock")
    code = (
        "from pathlib import Path; import time; from balatro_horizons.storage.journal import locked; "
        f"p=Path({str(lock)!r});\nwith locked(p): time.sleep(.3)"
    )
    process = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.monotonic() + 2
    while not lock.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    started = time.monotonic()
    Spending(path, 1).reserve("request", "episode", 0.1, 1)
    elapsed = time.monotonic() - started
    process.wait()
    assert elapsed >= 0.15
def test_reserve_event_precedes_send_and_unknown_usage_is_retained(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.04
    h.config.budgets.max_transport_attempts = 1
    h.failures.append(True)
    plan = h.plan(1)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    eid = batch_attempts(h.store, plan)[0]["episode_id"]
    assert h.ledger_rows and all(h.ledger_rows)
    assert all(
        any(row["episode_id"] == eid for row in rows.values())
        for rows in h.ledger_rows
    )
    ledger = json.loads((h.store.root / "batches" / plan["batch_id"] / "spending.json").read_text())
    assert ledger and all(not row["settled"] for row in ledger.values())


@pytest.mark.parametrize("retention_error", [KeyError, ValueError])
def test_retention_failure_preserves_provider_error(harness, monkeypatch, retention_error):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.04
    h.config.budgets.max_transport_attempts = 1
    h.failures.append(True)
    retain = Mock(side_effect=retention_error("inconsistent reservation"))
    monkeypatch.setattr(Spending, "retain", retain)
    plan = h.plan(1)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    attempts = batch_attempts(h.store, plan)
    assert len(attempts) == len(h.calls) == retain.call_count == 2
    for attempt in attempts:
        events = h.store.events(attempt["episode_id"])
        errors = [event for event in events if event["type"] == "provider_error"]
        assert len(errors) == 1
        retain.assert_any_call(errors[0]["request_id"])
        assert errors[0]["payload"]["code"] == "PROVIDER_TRANSPORT_UNKNOWN"
        assert errors[0]["payload"]["usage"] == "unknown"
        assert attempt["summary"]["outcome"] == "INFRASTRUCTURE_FAILURE"
        assert attempt["summary"]["reason"] == "PROVIDER_TRANSPORT_UNKNOWN"


def test_non_cost_limits_authorization_and_baselines_remain_unchanged(harness, monkeypatch):
    h = harness
    h.config.budgets.paid_calls_enabled = False
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = None
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plan = h.plan(1, ["heuristic"])
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert batch_attempts(h.store, plan)[0]["summary"]["outcome"] == "WIN"
    assert not h.calls and not h.clients


def test_provider_call_limit_remains_a_budget_outcome(harness):
    h = harness
    h.config.budgets.max_provider_calls = 1
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert all(row["summary"]["reason"] == "PROVIDER_CALL_LIMIT" for row in batch_attempts(h.store, plan))
def test_cache_write_price_is_used_for_reservation():
    config = luna()
    model = config.models["luna"].model_copy(
        update={"cached_input_usd_per_million": 0.02, "cache_write_input_usd_per_million": 0.4}
    )
    assert reservation_usd(model, config.budgets) == 0.0229376


def test_stopped_batch_revalidates_configuration_and_evidence_kind(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    h.config.budgets.max_batch_cost_usd = 2
    with pytest.raises(ValueError, match="^BATCH_CONFIGURATION_CHANGED$"):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    h.config.budgets.max_batch_cost_usd = 0.005
    with pytest.raises(ValueError, match="^BATCH_EVIDENCE_KIND_CHANGED$"):
        h.service().run_batch(h.config, plan["batch_id"], offline=False)
    assert not h.calls and not h.games and not h.clients


@pytest.mark.parametrize(
    "corruption",
    [
        "truncated", "wrong_batch", "missing_context", "unreadable",
        "EPISODE_COST_CAP", "BUDGET_EXTENSION_PARENT_CHANGED",
    ],
)
def test_corrupt_or_unreadable_stop_fails_closed(harness, monkeypatch, corruption):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    path = h.store.root / "batches" / plan["batch_id"] / "stop.json"
    value = json.loads(path.read_text())
    if corruption == "truncated":
        path.write_text('{"schema_version":')
    elif corruption == "wrong_batch":
        value["batch_id"] = "a" * 32
        path.write_text(json.dumps(value))
    elif corruption == "missing_context":
        del value["cost_context"]
        path.write_text(json.dumps(value))
    elif corruption in ("EPISODE_COST_CAP", "BUDGET_EXTENSION_PARENT_CHANGED"):
        value.update(
            stage="episode", reason=corruption, outcome="BUDGET_EXHAUSTED",
            episode_id="b" * 32,
            terminal={
                "event_id": "c" * 32, "hash": "d" * 64,
                "sequence": 1, "timestamp": value["recorded_at"],
            },
        )
        path.write_text(json.dumps(value))
        # Exercise the semantic guard independently of the Literal field parser.
        expected = (
            "INVALID_EPISODE_STOP" if corruption == "EPISODE_COST_CAP"
            else "UNKNOWN_REFUSAL_REASON"
        )
        with pytest.raises(ValueError, match=f"^{expected}$"):
            SchedulingStop.model_construct(**value).consistent_stage()
    else:
        original_read = type(path).read_text

        def denied(self, *args, **kwargs):
            if self == path:
                raise PermissionError("simulated unreadable stop")
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(type(path), "read_text", denied)
    before = path.read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert path.read_bytes() == before
    assert not h.games and not h.calls and not h.clients


@pytest.mark.parametrize("branch_outcome", ["WIN", "CAMPAIGN_INTERRUPTED"])
def test_assisted_children_do_not_resolve_slots_or_stop_scheduling(harness, branch_outcome, tmp_path):
    h = harness
    plan = h.plan(1)
    slot = plan["slots"][0]
    eid = h.store.create(
        {
            **slot,
            "batch_id": plan["batch_id"],
            "evidence_kind": "SYNTHETIC_TEST",
            "evaluation_eligible": False,
            "parent_episode_id": "a" * 32,
            "assistance": "human_override",
            "config": h.config.public(),
        }
    )
    h.store.finish(
        eid,
        {"outcome": branch_outcome, "reason": "CAMPAIGN_COST_CAP", "cost_usd": 20},
    )
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    assert report["scheduling_stop"] is None
    assert report["agents"]["luna"]["attempts"] == 1
    assert report["agents"]["luna"]["all_attempt_cost_usd"] == pytest.approx(0.0022)
    export_batch(h.store, plan["batch_id"], tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert len(bundle["episodes"]) == 1
    assert bundle["episodes"][0]["manifest"]["episode_id"] != eid
    assert len(h.games) == 1


def test_native_dispatch_rejects_unfunded_slot_before_game_constructor(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=False)
    assert not h.native.called
    assert not h.games and not h.calls and not h.clients and not h.store.list_episodes()
    assert read_stop(h.store, plan)["stage"] == "preflight"


@pytest.mark.parametrize("invalid", [0, -1, math.inf, math.nan, True])
def test_invalid_caps_are_configuration_errors(harness, invalid):
    h = harness
    h.config.budgets.max_episode_cost_usd = invalid
    with pytest.raises(ValueError, match="^PAID_EXECUTION_NOT_AUTHORIZED$"):
        h.service().execute(h.config, "luna", "fixture", offline=True)
    assert not h.games and not h.clients and not h.store.list_episodes()


def test_missing_provider_credential_is_checked_before_funding_stop(harness, monkeypatch):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plan = h.plan()
    with pytest.raises(ValueError, match="^MISSING_PROVIDER_CREDENTIAL$"):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert read_stop(h.store, plan) is None
    assert not h.games and not h.calls and not h.clients


def test_standalone_run_honors_campaign_cap(harness):
    h = harness
    h.config.budgets.max_episode_cost_usd = 1
    h.config.budgets.max_batch_cost_usd = 0.002
    summary = h.service().execute(h.config, "luna", "fixture", offline=True)
    assert summary["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert summary["reason"] == "CAMPAIGN_COST_CAP"
    assert summary["provider_calls"] == 0 and summary["cost_usd"] == 0
    assert len(h.games) == 1 and not h.calls


def test_required_ledger_guard_precedes_episode_creation(store, config):
    runner = Runner(store, config, FakeGame("MISSING_LEDGER"), Baseline("heuristic"), None)
    with pytest.raises(TypeError, match="^SPENDING_LEDGER_REQUIRED$"):
        runner.run()
    assert store.list_episodes() == []


def test_committed_terminal_takes_precedence_over_execute_exception(harness, monkeypatch):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0186384
    plan = h.plan()
    service = h.service()
    execute = service.execute

    def execute_then_raise(*args, **kwargs):
        execute(*args, **kwargs)
        raise RuntimeError("failure after committed terminal")

    monkeypatch.setattr(service, "execute", execute_then_raise)
    service.run_batch(h.config, plan["batch_id"], offline=True)
    assert read_stop(h.store, plan)["reason"] == "CAMPAIGN_COST_CAP"
    assert len(h.calls) == 2 and len(h.games) == 1
