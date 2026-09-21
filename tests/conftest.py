import httpx
import pytest

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.config import Config
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.runner import Runner
from balatro_horizons.storage.journal import Store


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must use a mock HTTP transport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def workbench_config():
    return Config(workbench_enabled=True)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def episode(store, config):
    private = {"seed": "DO_NOT_EXPORT_THIS_SEED", "config": config.model_dump()}
    return Runner(store, config, FakeGame(private["seed"]), Baseline("heuristic")).run(
        private=private
    )["episode_id"]


@pytest.fixture
def workbench_episode(store, workbench_config):
    private = {"seed": "DO_NOT_EXPORT_THIS_SEED", "config": workbench_config.model_dump()}
    return Runner(
        store, workbench_config, FakeGame(private["seed"]), Baseline("heuristic")
    ).run(private=private)["episode_id"]
