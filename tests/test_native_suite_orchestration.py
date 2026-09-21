"""Offline properties of the package-owned evidence orchestrator."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from balatro_horizons.cli import main as cli_main
from balatro_horizons.evidence import certification, stages
from balatro_horizons.evidence.collect import (
    action_table,
    faults,
    orchestrator,
    prefix,
    runs,
    runtime,
    settlement,
)
from balatro_horizons.game.contract import NativeFailure


class FakeGame:
    def __init__(self, session, environment):
        self.session = session
        self.environment = environment
        self.closed = False

    def close(self):
        assert not self.closed
        self.closed = True
        self.session.active_game = None
        self.session.games_closed += 1


class FakeSession:
    def __init__(self, owner, environment, reason):
        self.owner = owner
        self.environment = environment
        self.reason = reason
        self.active_game = None
        self.games_created = 0
        self.games_closed = 0
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        assert self.active_game is None
        self.owner.sessions_closed += 1

    def new_game(self, environment, seed):
        assert self.entered and self.active_game is None
        assert environment.runtime == self.environment.runtime
        assert environment.port == self.environment.port
        self.games_created += 1
        self.owner.total_games += 1
        self.active_game = FakeGame(self, environment)
        return self.active_game


class FakeSessionFactory:
    def __init__(self):
        self.sessions = []
        self.sessions_closed = 0
        self.total_games = 0

    def __call__(self, environment, *, reason):
        assert reason == "startup"
        session = FakeSession(self, environment, reason)
        self.sessions.append(session)
        return session


class FaultGame:
    def __init__(self, session, games, rpc_events):
        self.session = session
        self.games = games
        self.rpc_events = rpc_events
        self._rpc = self.rpc
        self.original_rpc = self._rpc
        self.closed = False

    def rpc(self, method, params=None, request_id=None):
        self.rpc_events.append((self, method, request_id))
        if method == "bh_request_status":
            return {"status": "committed" if len(self.games) == 1 else "unknown"}
        return {"ok": True}

    def wait_ready(self):
        return None

    def observe_private(self):
        return {"same": True}

    @contextmanager
    def intercept_rpc_for_calibration(self, wrapper):
        original = self._rpc
        self._rpc = wrapper(original)
        try:
            yield
        finally:
            self._rpc = original

    def apply_public_action(self, action, issuer, request_id=None):
        try:
            self._rpc("select", {}, request_id)
        except NativeFailure:
            status = self._rpc("bh_request_status", {"request_id": request_id})
            if status["status"] != "committed":
                raise NativeFailure("ACTION_STATUS_UNKNOWN") from None

    def close(self):
        self.closed = True
        self.session.active_game = None
        self.session.games_closed += 1


class FaultSession(FakeSession):
    def __init__(self, owner, environment, reason, games, rpc_events):
        super().__init__(owner, environment, reason)
        self.games = games
        self.rpc_events = rpc_events

    def new_game(self, environment, seed):
        assert self.entered and self.active_game is None
        game = FaultGame(self, self.games, self.rpc_events)
        self.games.append(game)
        self.active_game = game
        self.games_created += 1
        self.owner.total_games += 1
        return game


class FaultFactory(FakeSessionFactory):
    def __init__(self, games, rpc_events):
        super().__init__()
        self.games = games
        self.rpc_events = rpc_events

    def __call__(self, environment, *, reason):
        session = FaultSession(self, environment, reason, self.games, self.rpc_events)
        self.sessions.append(session)
        return session


class FaultRunner:
    def __init__(self, store, config, game, policy):
        self.game = game

    def run(self, *, manifest, private):
        try:
            self.game.apply_public_action(None, None, "request")
        except NativeFailure:
            outcome, actions = "INFRASTRUCTURE_FAILURE", 0
        else:
            outcome, actions = "WIN", 1
        self.game.close()
        return {"episode_id": "episode", "outcome": outcome, "committed_actions": actions}


def _environment(stake):
    return SimpleNamespace(
        stake=stake,
        deck="RED",
        runtime="native-runtime",
        powershell="powershell",
        port=12346,
        timeout_seconds=90,
    )


def test_plan_has_five_field_schema_and_frozen_totals():
    expected = {"name", "launches", "game_resets", "reasons", "collector", "artifact"}
    cases = [
        ({}, (12, 22)),
        ({"gameplay_only": True}, (1, 8)),
        ({"resume_actions": True}, (11, 16)),
        ({"resume_certification": True}, (11, 11)),
    ]
    for options, totals in cases:
        plan = stages.plan(**options)
        assert all(set(stage) == expected for stage in plan["stages"])
        assert (plan["expected_physical_launches"], plan["expected_game_resets"]) == totals
    assert all(
        stage["launches"] >= 0 and stage["game_resets"] >= 0 and stage["reasons"]
        for stage in plan["stages"]
    )


def test_cli_plan_and_action_table_match_goldens(capsys):
    fixtures = Path(__file__).parent / "fixtures"
    expected_plan = json.loads((fixtures / "evidence-plan.json").read_text())
    expected_actions = json.loads((fixtures / "evidence-shop-actions.json").read_text())
    assert cli_main(["evidence", "plan"]) == 0
    assert json.loads(capsys.readouterr().out) == expected_plan
    assert action_table.shop_action_types() == expected_actions


@pytest.mark.parametrize(
    ("flag", "totals"),
    [("--resume-actions", (11, 16)), ("--resume-certification", (11, 11))],
)
def test_cli_plan_exposes_resume_modes(capsys, flag, totals):
    assert cli_main(["evidence", "plan", flag]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert (plan["expected_physical_launches"], plan["expected_game_resets"]) == totals


def test_plan_is_nonexecuting_and_from_stage_selects_a_suffix(tmp_path, monkeypatch, capsys):
    def fail_if_touched(*_args, **_kwargs):
        raise AssertionError("evidence plan touched an execution dependency")

    monkeypatch.setattr("balatro_horizons.cli.commands.Store", fail_if_touched)
    monkeypatch.setattr("balatro_horizons.config.load_config", fail_if_touched)
    monkeypatch.setattr(
        "balatro_horizons.evidence.provenance.implementation_fingerprint",
        fail_if_touched,
    )
    for name in (
        "implementation_fingerprint",
        "load_config",
        "NativeSession",
        "collect_functional_cases",
        "collect_gameplay_only",
        "collect_profiles_and_functionals",
    ):
        monkeypatch.setattr(orchestrator, name, fail_if_touched)

    for argv in (
        ["evidence", "plan"],
        ["evidence", "plan", "--gameplay-only"],
        ["evidence", "plan", "--resume-actions"],
        ["evidence", "plan", "--resume-certification"],
    ):
        assert cli_main(argv) == 0
        assert json.loads(capsys.readouterr().out)["stages"]

    before = tuple(tmp_path.iterdir())
    plan = stages.plan()
    after = tuple(tmp_path.iterdir())
    assert before == after
    assert [stage.name for stage in stages.from_stage("functional collection")] == [
        "functional collection",
        "fresh-process restoration certification",
        "branch restoration",
    ]
    assert stages.from_stage("resumed functional collection")[0].launches == 1
    assert stages.from_stage("reorder acceptance fixture")[0].artifact == "native-reorder.json"
    assert plan["native_evidence_collected"] is False


@pytest.mark.parametrize(
    "options",
    [
        {"resume_certification": True, "resume_actions": True},
        {"gameplay_only": True, "resume_actions": True},
        {"gameplay_only": True, "resume_certification": True},
    ],
)
def test_plan_rejects_mutually_exclusive_modes(options):
    with pytest.raises(ValueError, match="RESUME_MODES_ARE_MUTUALLY_EXCLUSIVE"):
        stages.stage_list(**options)


def test_from_stage_dispatches_to_package_collector(monkeypatch, tmp_path):
    calls = []
    factory = FakeSessionFactory()
    config = SimpleNamespace(environment=_environment("GOLD"))
    monkeypatch.setattr(orchestrator, "implementation_fingerprint", lambda: "source")
    monkeypatch.setattr(orchestrator, "load_config", lambda path: config)
    monkeypatch.setattr(orchestrator, "_validate_prior_profiles", lambda root: "environment")
    monkeypatch.setattr(
        orchestrator,
        "_collect_functional_stage",
        lambda root, loaded, smoke, factory: calls.append((root, loaded, smoke, factory))
        or {"actions": {}, "win": {}},
    )
    result = orchestrator.collect(
        from_stage="functional collection", session_factory=factory, root=tmp_path
    )
    assert calls and calls[0][0] == tmp_path
    assert calls[0][3] is factory
    assert result["from_stage"] == "functional collection"
    monkeypatch.setattr(
        certification,
        "certify_release",
        lambda root, *, from_stage: {"root": root, "from_stage": from_stage},
    )
    assert orchestrator.collect(from_stage="branch restoration", root=tmp_path) == {
        "root": tmp_path.resolve(),
        "from_stage": "branch restoration",
    }
    with pytest.raises(ValueError, match="UNKNOWN_EVIDENCE_STAGE"):
        stages.from_stage("missing stage")


def test_gameplay_stage_requires_explicit_gameplay_only_flag(tmp_path):
    with pytest.raises(ValueError, match="GAMEPLAY_ONLY_REQUIRES_FLAG"):
        orchestrator.collect(from_stage=stages.GAMEPLAY_COLLECTION_ONLY, root=tmp_path)


def test_gameplay_only_writes_one_collection_artifact_and_skips_certification(
    monkeypatch, tmp_path
):
    writes = []

    def fail_certification(*_args, **_kwargs):
        raise AssertionError("gameplay-only collection invoked certification")

    monkeypatch.setattr(certification, "certify_release", fail_certification)
    monkeypatch.setattr(orchestrator, "implementation_fingerprint", lambda: "source")
    monkeypatch.setattr(orchestrator, "lock_digest", lambda root: "environment")
    monkeypatch.setattr(orchestrator, "load_config", lambda path: SimpleNamespace(environment=None))
    monkeypatch.setattr(
        orchestrator,
        "collect_gameplay_only",
        lambda config, smoke, factory: {
            "invalid": {"unchanged": True},
            "actions": {"actions": []},
            "win": {"outcome": "WIN"},
            "runs": {"pilot": {"outcome": "WIN"}},
            "reorder": {"status": "passed"},
            "faults": {"tests": []},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "atomic_json",
        lambda path, result, **kwargs: writes.append((path, result, kwargs)),
    )
    result = orchestrator.collect(gameplay_only=True, root=tmp_path)
    assert len(writes) == 1
    path, artifact, kwargs = writes[0]
    assert path.name.startswith("native-gameplay-collection-")
    assert kwargs["immutable"] is True
    assert artifact["evidence_scope"] == "gameplay_collection_only"
    assert artifact["native_release_evidence"] is False
    assert artifact["capability_certificates_created"] is False
    assert artifact["restoration_certification_included"] is False
    assert result["collection_only_artifact"] == str(path)


def test_profiles_and_functionals_reuse_processes_and_cleanup(monkeypatch):
    factory = FakeSessionFactory()
    configs = [
        SimpleNamespace(environment=_environment("WHITE")),
        SimpleNamespace(environment=_environment("GOLD")),
    ]

    def audit(configs, game_factory, *, persist_rules):
        for config in configs:
            game = game_factory(config.environment, config.environment.stake)
            game.close()
        return {config.environment.stake: {"persist_rules": persist_rules} for config in configs}

    monkeypatch.setattr(runtime, "audit_session", audit)
    monkeypatch.setattr(
        runtime,
        "write_profile_audits",
        lambda configs, samples: {
            config.environment.stake: {"fresh_processes": len(samples), "profile_stable": True}
            for config in configs
        },
    )

    def collect_functional(session):
        for index in range(8):
            game = session.new_game(configs[index % 2].environment, str(index))
            game.close()
        return {"faults": {"unknown_status": True}}

    profiles, functional = orchestrator.collect_profiles_and_functionals(
        configs, factory, collect_functional
    )
    assert set(profiles) == {"WHITE", "GOLD"}
    assert [session.games_created for session in factory.sessions] == [2, 10]
    assert [session.games_closed for session in factory.sessions] == [2, 10]
    assert factory.sessions_closed == 2 and factory.total_games == 12
    assert functional["faults"]["unknown_status"] is True


def test_gameplay_only_uses_one_startup_and_eight_resets(monkeypatch):
    factory = FakeSessionFactory()
    smoke = SimpleNamespace(environment=_environment("WHITE"))
    pilot = SimpleNamespace(environment=_environment("GOLD"))
    calls = []

    def collect(config, game_factory, *, smoke_config):
        assert config is pilot and smoke_config is smoke
        for index in range(8):
            game = game_factory(smoke.environment, str(index))
            calls.append(game.session)
            game.close()
        return {"unknown_status_last": True}

    monkeypatch.setattr(orchestrator, "collect_functional_cases", collect)
    result = orchestrator.collect_gameplay_only(pilot, smoke, factory)
    assert result == {"unknown_status_last": True}
    assert len(factory.sessions) == factory.sessions_closed == 1
    assert factory.sessions[0].games_created == factory.sessions[0].games_closed == 8
    assert len(calls) == 8 and all(owner is factory.sessions[0] for owner in calls)


def test_fault_collection_restores_transport_and_keeps_unknown_last(monkeypatch):
    environment = _environment("WHITE")
    games = []
    rpc_events = []

    config = SimpleNamespace(environment=environment, public=lambda: {}, model_dump=lambda: {})
    monkeypatch.setattr(faults, "Store", lambda root: object())
    monkeypatch.setattr(faults, "continuation_fingerprint", lambda raw: "same-state")
    monkeypatch.setattr(faults, "atomic_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(faults, "Runner", FaultRunner)
    factory = FaultFactory(games, rpc_events)
    with factory(environment, reason="startup") as session:
        results = faults.collect(config, game_factory=session.new_game)

    assert [result["unknown_status"] for result in results] == [False, True]
    assert session.games_created == session.games_closed == 2
    assert all(game.closed and game._rpc == game.original_rpc for game in games)
    assert results[-1]["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert [entry[1] for entry in rpc_events if entry[0] is games[1]] == ["select", "select"]


def test_session_run_service_rejects_non_calibration_native_games():
    config = SimpleNamespace(environment=_environment("WHITE"))
    created = []
    service = runs.SessionRunService(
        object(), object(), lambda environment, seed: created.append((environment, seed)) or "lease"
    )
    with pytest.raises(ValueError, match="CALIBRATION_SESSION_REQUIRED"):
        service.create_game(config, "seed", calibration=False)
    assert created == []
    assert service.create_game(config, "seed", calibration=True) == "lease"
    assert created == [(config.environment, "seed")]


def test_failed_continuation_probe_stops_certification(monkeypatch):
    monkeypatch.setattr(prefix, "certify_prefix", lambda *args, **kwargs: {"status": "failed"})
    with pytest.raises(ValueError, match="CONTINUATION_CERTIFICATION_FAILED"):
        certification.continuation_probe(
            object(), object(), "episode", restoration="seed_prefix"
        )


def test_failed_direct_checkpoint_probe_is_recorded_and_tolerated(monkeypatch):
    monkeypatch.setattr(
        certification,
        "verify_checkpoint",
        lambda *args, **kwargs: {"status": "failed", "mode": "checkpoint"},
    )
    assert certification.continuation_probe(
        object(), object(), "episode", restoration="checkpoint"
    )["status"] == "failed"


@pytest.mark.parametrize("defect, code", [
    ("source", "STALE_SETTLEMENT_SOURCE"),
    ("environment", "STALE_SETTLEMENT_ENVIRONMENT"),
])
def test_settlement_evidence_binds_every_episode_checkpoint(monkeypatch, tmp_path, defect, code):
    reports = tmp_path / "reports/verification"
    reports.mkdir(parents=True)
    source = "source"
    environment = {"runtime": "pinned"}
    environment_hash = certification.digest(environment)
    release = {
        "fixture": {"episode_id": "fixture"},
        "ordinary_runs": {"pilot": {"episode_id": "pilot"}},
        "implementation_hash": source,
        "environment_hash": environment_hash,
    }
    (reports / "native-faults.json").write_text(
        json.dumps({"tests": [{"episode_id": "fault"}]})
    )
    (reports / "native-reorder.json").write_text(
        json.dumps({
            "episode_id": "reorder",
            "implementation_hash": source,
            "environment_hash": environment_hash,
        })
    )
    episode_ids = {"fixture", "pilot", "fault", "reorder"}
    bad_id = "pilot" if defect == "source" else "fault"

    def checkpoint(_store, episode_id, _decision):
        return {
            "implementation_hash": source,
            "game": {"environment": environment},
        }

    monkeypatch.setattr(certification, "read_checkpoint", checkpoint)
    monkeypatch.setattr(settlement, "verify", lambda _store, ids: {"episodes": ids})
    result = certification._settlement_evidence(tmp_path, object(), release)
    assert result["status"] == "passed"
    assert set(result["checks"]["episodes"]) == episode_ids

    if defect == "source":
        def checkpoint(_store, episode_id, _decision):
            return {
                "implementation_hash": "changed" if episode_id == bad_id else source,
                "game": {"environment": environment},
            }
    else:
        def checkpoint(_store, episode_id, _decision):
            return {
                "implementation_hash": source,
                "game": {
                    "environment": {"runtime": "changed"}
                    if episode_id == bad_id
                    else environment
                },
            }
    monkeypatch.setattr(certification, "read_checkpoint", checkpoint)
    with pytest.raises(ValueError, match=code):
        certification._settlement_evidence(tmp_path, object(), release)
