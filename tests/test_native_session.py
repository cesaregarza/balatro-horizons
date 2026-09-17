from copy import deepcopy
from unittest.mock import Mock

import pytest

from balatro_horizons.config import Environment
from balatro_horizons.engine import native


@pytest.fixture
def bridges(monkeypatch):
    state = {"bh": {"ready": True, "busy": False, "profile": {"wins": 0}}}
    created = []

    def factory(environment):
        bridge = Mock(instance_id=None, calibration=False, env=environment)
        bridge.verify_files.return_value = {"pinned": True}
        bridge.rpc.side_effect = lambda *a, **k: deepcopy(state)

        def launch():
            bridge.instance_id = "owned-process"
            return deepcopy(state)

        bridge.launch.side_effect = launch
        created.append(bridge)
        return bridge

    monkeypatch.setattr(native, "WindowsBridge", factory)
    return created, state


def test_games_share_one_process_with_isolated_channels_and_explicit_ownership(bridges):
    created, _ = bridges
    environment = Environment()
    with native.NativeSession(environment) as session:
        first = session.new_game(environment, "FIRST")
        with pytest.raises(native.NativeFailure, match="ALREADY_ACTIVE"):
            session.new_game(environment, "OVERLAP")
        first.bridge.rpc = Mock(side_effect=AssertionError("old fault wrapper"))
        first.close()
        second = session.new_game(environment.model_copy(update={"stake": "WHITE"}), "SECOND")
        assert first.bridge is not second.bridge
        assert second.bridge.instance_id == "owned-process"
        assert [c.args[0] for c in second.bridge.rpc.call_args_list][:3] == [
            "bh_inspect",
            "menu",
            "start",
        ]
        second.bridge.verify_identity.assert_called()
        assert second.bridge.rpc.call_args_list[2].args[1]["seed"] == "SECOND"
        second.close()
        second.close()
        created[0].stop.assert_not_called()
    assert sum(b.launch.call_count for b in created) == 1
    assert sum(b.stop.call_count for b in created) == 1
    created[0].stop.assert_called_once()
    session.close()
    created[0].stop.assert_called_once()


def test_failed_identity_prevents_reset_and_retires_session(bridges):
    created, _ = bridges
    environment = Environment()
    with native.NativeSession(environment) as session:
        # Inject a wrong instance on the next lease, before its first mutation.
        real = native.WindowsBridge

        def factory(env):
            bridge = real(env)
            bridge.verify_identity.side_effect = native.NativeFailure(
                "NATIVE_PROCESS_IDENTITY_MISMATCH"
            )
            return bridge

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(native, "WindowsBridge", factory)
            with pytest.raises(native.NativeFailure, match="IDENTITY_MISMATCH"):
                session.new_game(environment, "SEED")
        assert [c.args[0] for c in created[-1].rpc.call_args_list] == ["bh_inspect"]
        with pytest.raises(native.NativeFailure, match="SESSION_UNAVAILABLE"):
            session.new_game(environment, "AGAIN")
    assert sum(b.launch.call_count for b in created) == 1


def test_unknown_action_status_retires_process_instead_of_relaunching(bridges):
    created, _ = bridges
    environment = Environment()
    with native.NativeSession(environment) as session:
        game = session.new_game(environment, "SEED")
        game.bridge.rpc.side_effect = [native.NativeFailure("LOST_ACK"), {"status": "unknown"}]
        with pytest.raises(native.NativeFailure, match="ACTION_STATUS_UNKNOWN"):
            game.apply_public_action(Mock(type="select_blind"), Mock())
        with pytest.raises(native.NativeFailure, match="SESSION_UNAVAILABLE"):
            game.apply_public_action(Mock(type="select_blind"), Mock())
        game.close()
        with pytest.raises(native.NativeFailure, match="SESSION_UNAVAILABLE"):
            session.new_game(environment, "NEXT")
    assert sum(b.launch.call_count for b in created) == 1


def test_context_exit_closes_unfinished_game_and_rejects_config_drift(bridges):
    created, _ = bridges
    environment = Environment()
    with native.NativeSession(environment) as session:
        with pytest.raises(native.NativeFailure, match="ENVIRONMENT_CHANGED"):
            session.new_game(environment.model_copy(update={"port": 54321}), "SEED")
        game = session.new_game(environment, "SEED")
    assert game._closed
    created[1]._close_rpc.assert_called_once()
    created[0].stop.assert_called_once()
    with pytest.raises(native.NativeFailure, match="NATIVE_GAME_CLOSED"):
        game.apply_public_action(Mock(type="select_blind"), Mock())


def test_profile_drift_fails_without_a_hidden_restart(bridges):
    created, state = bridges
    environment = Environment()
    with native.NativeSession(environment) as session:
        session.new_game(environment, "SEED").close()
        state["bh"]["profile"]["wins"] = 1
        with pytest.raises(native.NativeFailure, match="FROZEN_PROFILE_MISMATCH"):
            session.new_game(environment, "NEXT")
    assert sum(b.launch.call_count for b in created) == 1


def test_owned_default_game_keeps_its_launch_and_stop_contract(bridges):
    created, _ = bridges
    game = native.NativeGame(Environment(), "SEED", calibration=True)
    game.close()
    created[0].launch.assert_called_once()
    created[0].stop.assert_called_once()


def test_launch_failure_stops_only_the_attempted_owned_process(bridges):
    created, _ = bridges
    session = native.NativeSession(Environment())

    def fail():
        session.bridge.instance_id = "attempted-own-launch"
        raise native.NativeFailure("NATIVE_STARTUP_HANDSHAKE_TIMEOUT")

    session.bridge.launch.side_effect = fail
    with pytest.raises(native.NativeFailure, match="HANDSHAKE_TIMEOUT"):
        with session:
            pass
    created[0].stop.assert_called_once()


def test_only_declared_relaunch_reasons_are_accepted(bridges):
    with pytest.raises(ValueError, match="LAUNCH_REASON"):
        native.NativeSession(Environment(), reason="next_test")
    assert bridges[0] == []
