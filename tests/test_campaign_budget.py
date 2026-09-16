
"""Campaign funding is external to the resource-bounded agent protocol.

All games are synthetic; real provider request/parse logic uses mocked HTTP.
Prices are frozen regression fixtures, not current provider-price claims.
"""

import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest
from provider_transport import with_input_count
from test_openai_luna import luna, response

from balatro_horizons import service as service_module
from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.budget import BudgetExhausted, Spending, reservation_usd
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.batches import plan_batch
from balatro_horizons.evaluation.reports import export_batch, report_batch, scan
from balatro_horizons.evaluation.scheduling import batch_attempts, read_stop, record_stop
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store


@pytest.fixture
def harness(store, monkeypatch):
    config = luna()
    config.budgets.paid_calls_enabled = True
    config.budgets.max_episode_cost_usd = 1
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    games, calls, clients = [], [], []
    fail_transport, operations = [], []

    class CountedGame(FakeGame):
        def __init__(self, *args, **kwargs):
            games.append(self)
            super().__init__(*args, **kwargs)

    def receive(request):
        body = json.loads(request.content)
        calls.append(body)
        if fail_transport:
            raise httpx.ReadTimeout("synthetic transport failure")
        context = json.loads(body["input"][0]["content"])
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

    def fresh_service():
        return RunService(store, ReviewService(store))

    def plan(count=2, agents=None):
        return plan_batch(
            store,
            config,
            {"seeds": [f"BUDGETFIXTURE{i}" for i in range(count)]},
            agents or ["luna"],
            replicates=1,
        )

    yield type(
        "Harness",
        (),
        {
            "config": config,
            "store": store,
            "games": games,
            "calls": calls,
            "clients": clients,
            "operations": operations,
            "fail_transport": fail_transport,
            "native": native,
            "service": staticmethod(fresh_service),
            "plan": staticmethod(plan),
        },
    )()
    for client in clients:
        client.close()


def test_T01_unfunded_slot_is_unresolved_and_stop_survives_restart(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0184
    plan = h.plan()
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    report = report_batch(h.store, bid, tmp_path / "report")
    row = report["agents"]["luna"]
    assert {k: row[k] for k in ("planned", "attempts", "valid", "wins", "unresolved")} == {
        "planned": 2,
        "attempts": 1,
        "valid": 1,
        "wins": 1,
        "unresolved": 1,
    }
    assert row["win_rate"] == 1 and row["coverage"] == 0.5
    assert row["missing_outcome_bounds"] == [0.5, 1]
    stop = report["scheduling_stop"]
    assert stop["reason"] == "CAMPAIGN_COST_CAP" and stop["stage"] == "preflight"
    assert len(h.games) == 1 and len(h.calls) == 5
    stop_path = h.store.root / "batches" / bid / "stop.json"
    before = stop_path.read_bytes()
    h.service().run_batch(h.config, bid, offline=True)
    assert stop_path.read_bytes() == before
    assert len(h.games) == 1 and len(h.calls) == 5


def test_T02_campaign_interruption_keeps_cost_and_unresolved_slot(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0192
    plan = h.plan(3)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    attempts = batch_attempts(h.store, plan)
    interrupted = attempts[1]
    assert interrupted["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert interrupted["summary"]["reason"] == "CAMPAIGN_COST_CAP"
    assert interrupted["summary"]["committed_actions"] == 2
    assert interrupted["manifest"]["evaluation_eligible"]
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    row = report["agents"]["luna"]
    assert (row["attempts"], row["valid"], row["wins"], row["unresolved"]) == (2, 1, 1, 2)
    assert row["all_attempt_cost_usd"] == pytest.approx(0.00308)
    stop = report["scheduling_stop"]
    assert stop["episode_id"] == interrupted["episode_id"] and stop["stage"] == "episode"
    assert stop["terminal"]["hash"] == interrupted["terminal"]["hash"]
    assert len(h.games) == 2 and len(h.calls) == 7 and len(h.clients) == 2


def test_T03_episode_only_limit_resolves_slots_without_stopping_batch(harness, tmp_path):
    h = harness
    h.config.budgets.max_episode_cost_usd = 0.017
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    for attempt in batch_attempts(h.store, plan):
        assert attempt["summary"]["outcome"] == "BUDGET_EXHAUSTED"
        assert attempt["summary"]["reason"] == "EPISODE_COST_CAP"
        assert attempt["summary"]["committed_actions"] == 2
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    row = report["agents"]["luna"]
    assert (row["attempts"], row["valid"], row["wins"], row["unresolved"]) == (2, 2, 0, 0)
    assert report["scheduling_stop"] is None
    assert len(h.games) == 2 and len(h.calls) == 4


def test_T04_both_caps_resolve_current_slot_and_stickily_stop(harness, tmp_path):
    h = harness
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = 0.017
    plan = h.plan()
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    report = report_batch(h.store, bid, tmp_path / "report")
    row = report["agents"]["luna"]
    assert (row["attempts"], row["valid"], row["wins"], row["unresolved"]) == (1, 1, 0, 1)
    stop = report["scheduling_stop"]
    assert stop["outcome"] == "BUDGET_EXHAUSTED"
    assert stop["reason"] == "EPISODE_AND_CAMPAIGN_COST_CAP"
    path = h.store.root / "batches" / bid / "stop.json"
    before = path.read_bytes()
    h.service().run_batch(h.config, bid, offline=True)
    assert path.read_bytes() == before and len(h.calls) == 2 and len(h.games) == 1


def test_T05_transport_replacement_preserves_unknown_usage_reservations(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.04
    h.config.budgets.max_transport_attempts = 1
    h.fail_transport.append(True)
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    row = report["agents"]["luna"]
    assert row["attempt_outcomes"] == {"INFRASTRUCTURE_FAILURE": 2}
    assert row["valid"] == 0 and row["unresolved"] == 2
    assert row["all_attempt_cost_usd"] == pytest.approx(0.032768)
    assert report["scheduling_stop"]["stage"] == "preflight"
    assert report["scheduling_stop"]["cost_context"]["unsettled_usd"] == 0.032768
    assert len(h.games) == 2 and len(h.calls) == 2


def add_pricey_model(h):
    h.config.models["pricey"] = h.config.models["luna"].model_copy(
        update={
            "input_usd_per_million": 20.0,
            "output_usd_per_million": 120.0,
        }
    )
    assert reservation_usd(h.config.models["pricey"], h.config.budgets) == 1.6384


def test_T06_next_model_determines_preflight_and_cannot_be_skipped(harness):
    h = harness
    add_pricey_model(h)
    h.config.budgets.max_episode_cost_usd = 2
    h.config.budgets.max_batch_cost_usd = 0.02
    plan = h.plan(1, ["luna", "pricey", "heuristic"])
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    stop = read_stop(h.store, plan)
    assert stop["agent"] == "pricey" and stop["stage"] == "preflight"
    assert stop["cost_context"]["required_usd"] == 1.6384
    assert len(h.calls) == 5 and len(h.games) == 1 and len(h.clients) == 1


def test_T07_fresh_service_cannot_resume_with_cheaper_or_free_model(harness):
    h = harness
    add_pricey_model(h)
    h.config.budgets.max_episode_cost_usd = 2
    h.config.budgets.max_batch_cost_usd = 1.70
    plan = h.plan(1, ["pricey", "luna", "heuristic"])
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    (attempt,) = batch_attempts(h.store, plan)
    assert attempt["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert attempt["summary"]["cost_usd"] == pytest.approx(0.088)
    before = (h.store.root / "batches" / bid / "stop.json").read_bytes()
    h.service().run_batch(h.config, bid, offline=True)
    assert (h.store.root / "batches" / bid / "stop.json").read_bytes() == before
    assert len(h.calls) == 2 and len(h.games) == 1


@pytest.mark.parametrize("index_state", ["stale", "missing"])
def test_T08_recover_committed_terminal_before_index_or_stop_write(
    harness,
    monkeypatch,
    index_state,
    tmp_path,
):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.017
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
def test_T10_invalid_paid_configuration_never_constructs_episode_or_game(
    harness,
    entrypoint,
    episode_cap,
    campaign_cap,
    reason,
):
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
    assert h.store.list_episodes() == []
    assert not h.games and not h.calls and not h.clients
    assert read_stop(h.store, plan) is None
    h.native.assert_not_called()


def test_T11_real_report_and_export_include_stop_and_interrupted_costs(harness, tmp_path):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.0192
    plan = h.plan(3)
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    report_batch(h.store, bid, tmp_path / "report")
    result = export_batch(h.store, bid, tmp_path / "export")
    report = json.loads((tmp_path / "report/report.json").read_text())
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert result["episodes"] == 2 and len(bundle["episodes"]) == 2
    assert bundle["report"] == report
    assert bundle["plan"]["slots"] == plan["slots"]
    row = report["agents"]["luna"]
    assert row["unresolved"] == 2 and row["all_attempt_cost_usd"] == pytest.approx(0.00308)
    assert sum(e["summary"]["cost_usd"] for e in bundle["episodes"]) == pytest.approx(0.00308)
    (interrupted,) = [
        e for e in bundle["episodes"] if e["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    ]
    assert interrupted["manifest"]["evaluation_eligible"]
    assert interrupted["summary"]["committed_actions"] == 2
    scan(bundle, [f"BUDGETFIXTURE{i}" for i in range(3)])
    with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
        scan({**bundle, "injected": "BUDGETFIXTURE1"}, ["BUDGETFIXTURE1"])


def test_T12_stopped_batch_still_validates_frozen_config_and_evidence_kind(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    h.config.budgets.max_batch_cost_usd = 2
    with pytest.raises(ValueError, match="BATCH_CONFIGURATION_CHANGED"):
        h.service().run_batch(h.config, bid, offline=True)
    h.config.budgets.max_batch_cost_usd = 0.005
    with pytest.raises(ValueError, match="BATCH_EVIDENCE_KIND_CHANGED"):
        h.service().run_batch(h.config, bid, offline=False)
    assert not h.calls and not h.games
    h.native.assert_not_called()


@pytest.mark.parametrize(
    "limit,value,reason",
    [
        ("max_game_actions", 1, "GAME_ACTION_LIMIT"),
        ("max_provider_calls", 1, "PROVIDER_CALL_LIMIT"),
        ("max_helper_calls_per_decision", 0, "HELPER_CALL_LIMIT"),
    ],
)
def test_T12_non_cost_resource_limits_remain_valid_outcomes(
    harness, tmp_path, limit, value, reason
):
    h = harness
    setattr(h.config.budgets, limit, value)
    if limit == "max_helper_calls_per_decision":
        h.operations.append({"kind": "arithmetic", "expression": "2+2"})
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    for row in batch_attempts(h.store, plan):
        assert row["summary"]["reason"] == reason
        assert row["summary"]["outcome"] == "BUDGET_EXHAUSTED"
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    assert report["scheduling_stop"] is None
    assert report["agents"]["luna"]["valid"] == 2
    assert len(h.games) == 2


def test_T12_legacy_generic_cost_stop_is_not_reinterpreted(harness, tmp_path):
    h = harness
    plan = h.plan(1)
    slot = plan["slots"][0]
    eid = h.store.create(
        {
            **slot,
            "batch_id": plan["batch_id"],
            "evidence_kind": "SYNTHETIC_TEST",
            "evaluation_eligible": True,
            "config": h.config.public(),
        },
        {"seed": "BUDGETFIXTURE0"},
    )
    h.store.finish(
        eid,
        {
            "outcome": "BUDGET_EXHAUSTED",
            "reason": "COST_CAP_REACHED",
            "cost_usd": 0.12,
        },
    )
    before = (h.store.episode_path(eid) / "events.jsonl").read_bytes()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    export_batch(h.store, plan["batch_id"], tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert bundle["report"] == report and report["scheduling_stop"] is None
    assert report["agents"]["luna"]["valid"] == 1
    assert h.store.summary(eid)["reason"] == "COST_CAP_REACHED"
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
    assert not h.games and not h.calls


@pytest.mark.parametrize("branch_outcome", ["WIN", "CAMPAIGN_INTERRUPTED"])
def test_assisted_children_neither_resolve_slots_nor_stop_scheduling(
    harness, tmp_path, branch_outcome
):
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
        {
            "outcome": branch_outcome,
            "reason": "CAMPAIGN_COST_CAP",
            "cost_usd": 20,
        },
    )
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    report = report_batch(h.store, plan["batch_id"], tmp_path / "report")
    assert report["scheduling_stop"] is None
    row = report["agents"]["luna"]
    assert row["attempts"] == 1 and row["wins"] == 1
    assert row["all_attempt_cost_usd"] == pytest.approx(0.0022)
    export_batch(h.store, plan["batch_id"], tmp_path / "export")
    bundle = json.loads((tmp_path / "export/public.json").read_text())
    assert len(bundle["episodes"]) == 1 and bundle["episodes"][0]["manifest"]["episode_id"] != eid
    assert len(h.games) == 1


@pytest.mark.parametrize(
    "corruption", ["truncated", "wrong_batch", "missing_context", "unreadable"]
)
def test_corrupt_or_unreadable_stop_fails_closed(harness, monkeypatch, corruption):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    bid = plan["batch_id"]
    h.service().run_batch(h.config, bid, offline=True)
    path = h.store.root / "batches" / bid / "stop.json"
    value = json.loads(path.read_text())
    if corruption == "truncated":
        path.write_text('{"schema_version":')
    elif corruption == "wrong_batch":
        value["batch_id"] = "a" * 32
        path.write_text(json.dumps(value))
    elif corruption == "missing_context":
        del value["cost_context"]
        path.write_text(json.dumps(value))
    else:
        read = type(path).read_text

        def denied(self, *args, **kwargs):
            if self == path:
                raise PermissionError("simulated unreadable stop")
            return read(self, *args, **kwargs)

        monkeypatch.setattr(type(path), "read_text", denied)
    before = path.read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        h.service().run_batch(h.config, bid, offline=True)
    assert path.read_bytes() == before
    assert not h.games and not h.calls and not h.clients


def test_first_stop_cannot_be_replaced(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    original = read_stop(h.store, plan)
    path = h.store.root / "batches" / plan["batch_id"] / "stop.json"
    before = path.read_bytes()
    later = record_stop(
        h.store,
        plan,
        reason="CAMPAIGN_COST_CAP",
        stage="preflight",
        slot_id=plan["slots"][1]["slot_id"],
        agent="luna",
        cost_context=original["cost_context"],
    )
    assert later == original and path.read_bytes() == before


def test_native_dispatch_rejects_unfunded_slot_before_any_game_constructor(harness):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    plan = h.plan()
    h.service().run_batch(h.config, plan["batch_id"], offline=False)
    h.native.assert_not_called()
    assert not h.games and not h.calls and not h.clients and not h.store.list_episodes()
    assert read_stop(h.store, plan)["stage"] == "preflight"


@pytest.mark.parametrize("invalid", [0, -1, math.inf, math.nan, True])
def test_invalid_caps_are_configuration_errors(harness, invalid):
    h = harness
    # Assignment bypasses Pydantic construction; admission must still reject it.
    h.config.budgets.max_episode_cost_usd = invalid
    with pytest.raises(ValueError, match="PAID_EXECUTION_NOT_AUTHORIZED"):
        h.service().execute(h.config, "luna", "fixture", offline=True)
    assert not h.games and not h.clients and not h.store.list_episodes()


def test_authorization_and_credentials_checked_before_funding_stop(harness, monkeypatch):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.005
    h.config.budgets.paid_calls_enabled = False
    plan = h.plan()
    with pytest.raises(ValueError, match="PAID_EXECUTION_NOT_AUTHORIZED"):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert read_stop(h.store, plan) is None
    h.config.budgets.paid_calls_enabled = True
    plan = h.plan()
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="MISSING_PROVIDER_CREDENTIAL"):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert read_stop(h.store, plan) is None and not h.games and not h.clients


def test_scripted_baseline_does_not_require_paid_configuration(harness, monkeypatch):
    h = harness
    h.config.budgets.paid_calls_enabled = False
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = None
    monkeypatch.delenv("OPENAI_API_KEY")
    plan = h.plan(1, ["heuristic"])
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    (attempt,) = batch_attempts(h.store, plan)
    assert attempt["summary"]["outcome"] == "WIN" and not h.calls and not h.clients


def test_reservation_remains_authoritative_after_nonbinding_preflight(tmp_path):
    spending = Spending(tmp_path / "spend.json", 0.02)
    assert spending.affordability(0.016384)[0]
    assert not spending.path.exists()
    spending.reserve("competing", "another", 0.016384, 1)
    with pytest.raises(BudgetExhausted, match="^CAMPAIGN_COST_CAP$"):
        spending.reserve("later", "current", 0.016384, 1)
    assert set(json.loads(spending.path.read_text())) == {"competing"}


def test_recorded_overage_and_negative_headroom_are_not_hidden(tmp_path):
    spending = Spending(tmp_path / "spend.json", 0.02)
    spending.reserve("overage", "previous", 0.01, 1)
    spending.settle("overage", 0.03)
    affordable, context = spending.affordability(0.01)
    assert not affordable and context["campaign_committed_usd"] == 0.03
    assert context["campaign_headroom_usd"] == pytest.approx(-0.01)


def test_resumed_episode_cost_is_part_of_episode_limit(tmp_path):
    spending = Spending(tmp_path / "spend.json", 1)
    with pytest.raises(BudgetExhausted, match="^EPISODE_COST_CAP$") as error:
        spending.reserve("branch", "child", 0.016384, 0.017, prior_cost=0.00088)
    assert error.value.outcome == "BUDGET_EXHAUSTED"
    assert not spending.path.exists()


def test_concurrent_stop_writers_observe_the_same_first_record(harness):
    h = harness
    plan = h.plan()
    _, context = Spending(h.store.root / "ledger.json", 0.005).affordability(0.016384)

    def write(slot):
        return record_stop(
            h.store,
            plan,
            reason="CAMPAIGN_COST_CAP",
            stage="preflight",
            slot_id=slot["slot_id"],
            agent=slot["agent"],
            cost_context=context,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(write, plan["slots"]))
    assert first == second == read_stop(h.store, plan)


def test_terminal_committed_before_stop_write_failure_is_recovered(harness, monkeypatch):
    from balatro_horizons.evaluation import scheduling

    h = harness
    h.config.budgets.max_batch_cost_usd = 0.017
    plan = h.plan()
    write = scheduling.atomic_json
    monkeypatch.setattr(scheduling, "atomic_json", Mock(side_effect=OSError("simulated crash")))
    with pytest.raises(OSError, match="simulated crash"):
        h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert h.store.list_episodes()[0]["summary"]["outcome"] == "CAMPAIGN_INTERRUPTED"
    assert read_stop(h.store, plan) is None
    monkeypatch.setattr(scheduling, "atomic_json", write)
    h.service().run_batch(h.config, plan["batch_id"], offline=True)
    assert read_stop(h.store, plan)["recovered"]
    assert len(h.calls) == 2 and len(h.games) == 1


def test_committed_terminal_takes_precedence_over_execute_exception(harness, monkeypatch):
    h = harness
    h.config.budgets.max_batch_cost_usd = 0.017
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


def test_reservation_uses_maximum_cache_write_rate():
    config = luna()
    model = config.models["luna"].model_copy(
        update={
            "cached_input_usd_per_million": 0.02,
            "cache_write_input_usd_per_million": 0.4,
        }
    )
    assert reservation_usd(model, config.budgets) == 0.0229376
