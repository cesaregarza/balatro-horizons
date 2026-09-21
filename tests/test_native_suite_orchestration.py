import importlib
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.storage.journal import digest


def _modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return (
        importlib.import_module("verify_release"),
        importlib.import_module("audit_runtime"),
    )


class FakeGame:
    def __init__(self, session, environment):
        self.session = session
        self.environment = environment
        self.raw = {"bh": {"profile": {"stake": environment.stake, "fresh": True}}}
        self.lock = {"pinned": "same-environment"}
        self.bridge = SimpleNamespace(rpc=self.rpc)
        self.closed = False

    def rpc(self, method, params=None):
        assert method == "bh_rules"
        return {"rules": {"one": {"name": "One rule"}}}

    def inspect_raw(self):
        return self.raw

    def rules(self):
        return self.rpc("bh_rules")

    def wait_ready(self):
        return None

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
        assert self.entered
        assert self.active_game is None
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


def _environment(stake):
    return SimpleNamespace(
        stake=stake,
        deck="RED",
        runtime="native-runtime",
        powershell="powershell",
        port=12346,
        timeout_seconds=90,
    )


def test_plan_is_nonexecuting_and_reports_expected_launches(monkeypatch, capsys):
    release, _ = _modules(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("plan touched runtime inputs or native services")

    monkeypatch.setattr(sys, "argv", ["verify_release.py", "--plan"])
    monkeypatch.setattr(release, "Store", forbidden)
    monkeypatch.setattr(release, "implementation_fingerprint", forbidden)
    monkeypatch.setattr(release, "load_config", forbidden)
    release.main(session_factory=forbidden)
    plan = json.loads(capsys.readouterr().out)

    assert plan["native_evidence_collected"] is False
    assert plan["expected_physical_launches"] == 12
    assert plan["expected_game_resets"] == 22
    assert plan["stages"][0]["stakes_per_process"] == ["WHITE", "GOLD"]
    assert plan["stages"][1]["physical_launches"] == 0
    assert plan["stages"][1]["cases"][-1] == "unknown action status (last)"
    assert plan["stages"][2]["game_resets"] == 9

    monkeypatch.setattr(sys, "argv", ["verify_release.py", "--plan", "--gameplay-only"])
    release.main(session_factory=forbidden)
    gameplay_plan = json.loads(capsys.readouterr().out)
    assert gameplay_plan["native_evidence_collected"] is False
    assert gameplay_plan["release_certification_requested"] is False
    assert gameplay_plan["expected_physical_launches"] == 1
    assert gameplay_plan["expected_game_resets"] == 8
    assert gameplay_plan["stages"][0]["capability_activation"] is False
    assert gameplay_plan["stages"][0]["cases"][-1] == "unknown action status (last)"


@pytest.mark.parametrize(
    "options",
    [
        ["--plan", "--resume-certification", "--resume-actions", "episode"],
        ["--gameplay-only", "--resume-actions", "episode"],
        ["--gameplay-only", "--resume-certification"],
    ],
)
def test_plan_validates_mutually_exclusive_resume_boundaries(monkeypatch, options):
    release, _ = _modules(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["verify_release.py", *options])
    with pytest.raises(SystemExit) as failure:
        release.main()
    assert failure.value.code == 2


def test_profiles_and_functional_cases_reuse_processes_and_release_every_game(monkeypatch):
    release, audit = _modules(monkeypatch)
    factory = FakeSessionFactory()
    writes = []
    monkeypatch.setattr(audit, "atomic_json", lambda *args, **kwargs: writes.append(args[0]))

    configs = [
        SimpleNamespace(environment=_environment("WHITE")),
        SimpleNamespace(environment=_environment("GOLD")),
    ]
    functional_config = configs[1]
    smoke_config = configs[0]
    case_events = []

    def one_game(label, config, game_factory):
        case_events.append((label, game_factory.__self__))
        game = game_factory(config.environment, label)
        game.close()

    monkeypatch.setattr(
        release,
        "invalid_tests",
        lambda config, *, game_factory: one_game("invalid", config, game_factory),
    )
    monkeypatch.setattr(
        release,
        "exercise_shop",
        lambda config, *, game_factory: one_game("shop", config, game_factory),
    )
    monkeypatch.setattr(
        release,
        "exercise_win",
        lambda config, *, game_factory: one_game("terminal", config, game_factory),
    )

    def ordinary(*, game_factory):
        for config in configs:
            one_game("ordinary_" + config.environment.stake, config, game_factory)
        return {"ordinary": True}

    monkeypatch.setattr(release, "ordinary_runs", ordinary)
    monkeypatch.setattr(
        release,
        "exercise_reorder",
        lambda config, *, game_factory: one_game("reorder", config, game_factory),
    )

    def faults(config, *, game_factory):
        one_game("lost_ack", config, game_factory)
        one_game("unknown_status", config, game_factory)
        return {"faults": True}

    monkeypatch.setattr(release, "fault_tests", faults)

    profiles, functional = release.collect_profiles_and_functionals(
        configs,
        factory,
        lambda session: release.collect_functional_cases(
            functional_config,
            session.new_game,
            smoke_config=smoke_config,
        ),
    )

    assert set(profiles) == {"WHITE", "GOLD"}
    assert all(item["fresh_processes"] == 2 for item in profiles.values())
    assert all(item["profile_stable"] for item in profiles.values())
    assert profiles["WHITE"]["environment_hash"] == digest({"pinned": "same-environment"})
    assert len(factory.sessions) == 2
    assert factory.sessions_closed == 2
    assert [session.games_created for session in factory.sessions] == [2, 10]
    assert [session.games_closed for session in factory.sessions] == [2, 10]
    assert factory.total_games == 12
    assert [event[0] for event in case_events] == [
        "invalid",
        "shop",
        "terminal",
        "ordinary_WHITE",
        "ordinary_GOLD",
        "reorder",
        "lost_ack",
        "unknown_status",
    ]
    second_session = factory.sessions[1]
    assert all(owner is second_session for _, owner in case_events)
    assert functional["faults"] == {"faults": True}


def test_gameplay_only_collection_uses_one_startup_and_eight_resets(monkeypatch):
    release, _ = _modules(monkeypatch)
    factory = FakeSessionFactory()
    smoke = SimpleNamespace(environment=_environment("WHITE"))
    pilot = SimpleNamespace(environment=_environment("GOLD"))
    created_by = []

    def collect(config, game_factory, *, smoke_config):
        assert config is pilot
        assert smoke_config is smoke
        for index in range(8):
            created_by.append(game_factory.__self__)
            game = game_factory(
                smoke.environment if index % 2 == 0 else pilot.environment, str(index)
            )
            game.close()
        return {"unknown_status_last": True}

    monkeypatch.setattr(release, "collect_functional_cases", collect)
    result = release.collect_gameplay_only(pilot, smoke, factory)

    assert result == {"unknown_status_last": True}
    assert len(factory.sessions) == 1
    assert factory.sessions_closed == 1
    assert factory.sessions[0].games_created == 8
    assert factory.sessions[0].games_closed == 8
    assert len(created_by) == 8
    assert all(owner is factory.sessions[0] for owner in created_by)


def test_gameplay_only_main_writes_collection_artifact_and_skips_certification(
    monkeypatch, tmp_path, capsys
):
    release, _ = _modules(monkeypatch)
    root = tmp_path
    (root / "private").mkdir()
    (root / "private/environment.lock.json").write_text('{"environment":"offline"}')
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "Store", lambda path: object())
    monkeypatch.setattr(release, "implementation_fingerprint", lambda: "same-source")
    monkeypatch.setattr(release, "digest", lambda value: "same-environment")
    monkeypatch.setattr(
        release,
        "load_config",
        lambda path: SimpleNamespace(
            environment=_environment("WHITE" if "smoke" in str(path) else "GOLD")
        ),
    )
    monkeypatch.setattr(
        release,
        "collect_gameplay_only",
        lambda *args: {
            "invalid": {"episode_id": "invalid"},
            "actions": {"episode_id": "actions"},
            "win": {"episode_id": "win"},
            "runs": {"pilot": {"episode_id": "ordinary"}},
            "reorder": {"episode_id": "reorder"},
            "faults": {"tests": [{"episode_id": "fault"}]},
        },
    )
    writes = []
    monkeypatch.setattr(
        release, "atomic_json", lambda path, data, **kwargs: writes.append((path, data))
    )
    monkeypatch.setattr(
        release,
        "command",
        lambda *args: (_ for _ in ()).throw(AssertionError("certification command was called")),
    )
    monkeypatch.setattr(sys, "argv", ["verify_release.py", "--gameplay-only"])

    release.main(
        session_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("main bypassed the mocked gameplay collector")
        )
    )
    output = json.loads(capsys.readouterr().out)

    assert len(writes) == 1
    artifact_path, artifact = writes[0]
    assert artifact_path.name.startswith("native-gameplay-collection-")
    assert artifact["evidence_scope"] == "gameplay_collection_only"
    assert artifact["native_release_evidence"] is False
    assert artifact["capability_certificates_created"] is False
    assert artifact["restoration_certification_included"] is False
    assert output["collection_only_artifact"] == str(artifact_path)


def test_fault_collection_restores_transport_and_ends_on_unknown_status(monkeypatch, tmp_path):
    _modules(monkeypatch)
    faults = importlib.import_module("native_faults")
    environment = _environment("WHITE")
    factory = FakeSessionFactory()
    games = []
    rpc_events = []

    class FaultGame:
        def __init__(self, session):
            self.session = session
            self.lock = {"pinned": "same-environment"}
            self.raw = {}
            self.bridge = SimpleNamespace(rpc=self.rpc)
            self.original_rpc = self.bridge.rpc
            self.closed = False

        def rpc(self, method, params=None, request_id=None):
            rpc_events.append((self, method, request_id))
            if method == "bh_request_status":
                return {"status": "committed" if len(games) == 1 else "unknown"}
            return {"ok": True}

        def wait_ready(self):
            return None

        def observe_private(self):
            return {"same": True}

        @contextmanager
        def intercept_rpc_for_calibration(self, wrapper):
            original = self.bridge.rpc
            self.bridge.rpc = wrapper(original)
            try:
                yield
            finally:
                self.bridge.rpc = original

        def close(self):
            self.closed = True
            self.session.active_game = None
            self.session.games_closed += 1

    class FaultSession(FakeSession):
        def new_game(self, env, seed):
            assert self.entered and self.active_game is None
            assert env.runtime == self.environment.runtime
            game = FaultGame(self)
            games.append(game)
            self.active_game = game
            self.games_created += 1
            self.owner.total_games += 1
            return game

    class FaultFactory(FakeSessionFactory):
        def __call__(self, env, *, reason):
            assert reason == "startup"
            session = FaultSession(self, env, reason)
            self.sessions.append(session)
            return session

    factory = FaultFactory()
    config = SimpleNamespace(
        environment=environment,
        budgets=SimpleNamespace(max_episode_cost_usd=1),
        public=lambda: {},
        model_dump=lambda: {},
    )
    monkeypatch.setattr(faults, "Store", lambda root: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(faults, "continuation_fingerprint", lambda raw: "same-state")
    monkeypatch.setattr(faults, "atomic_json", lambda *args, **kwargs: None)

    class RunnerStub:
        def __init__(self, store, config, game, policy, spending):
            self.game = game
            assert spending is not None

        def run(self, *, manifest, private):
            try:
                self.game.bridge.rpc("select", {}, "request")
            except faults.NativeFailure:
                status = self.game.bridge.rpc("bh_request_status", {"request_id": "request"})
                if status["status"] == "committed":
                    outcome, actions = "WIN", 1
                else:
                    outcome, actions = "INFRASTRUCTURE_FAILURE", 0
            else:
                raise AssertionError("injected acknowledgment loss did not occur")
            self.game.close()
            return {"episode_id": "episode", "outcome": outcome, "committed_actions": actions}

    monkeypatch.setattr(faults, "Runner", RunnerStub)
    with factory(environment, reason="startup") as session:
        results = faults.collect(config, game_factory=session.new_game)

    assert [result["unknown_status"] for result in results] == [False, True]
    assert len(factory.sessions) == 1 and factory.sessions_closed == 1
    assert session.games_created == session.games_closed == 2
    assert all(game.closed and game.bridge.rpc == game.original_rpc for game in games)
    unknown_events = [entry[1] for entry in rpc_events if entry[0] is games[1]]
    assert unknown_events == ["select", "select"]
    assert results[-1]["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert games[1] is games[-1]


def test_session_run_service_rejects_non_calibration_native_games(monkeypatch):
    _modules(monkeypatch)
    native_runs = importlib.import_module("native_runs")
    config = SimpleNamespace(environment=_environment("WHITE"))
    created = []
    service = native_runs.SessionRunService(
        object(), object(), lambda environment, seed: created.append((environment, seed)) or "lease"
    )

    with pytest.raises(ValueError, match="CALIBRATION_SESSION_REQUIRED"):
        service.create_game(config, "seed", calibration=False)
    assert created == []
    assert service.create_game(config, "seed", calibration=True) == "lease"
    assert created == [(config.environment, "seed")]
