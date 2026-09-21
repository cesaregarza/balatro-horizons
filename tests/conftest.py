import httpx
import pytest

from balatro_horizons.config import Config
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
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
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def episode(store, config):
    private = {"seed": "DO_NOT_EXPORT_THIS_SEED", "config": config.model_dump()}
    return Runner(
        store,
        config,
        FakeGame(private["seed"]),
        Baseline("heuristic"),
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run(
        private=private
    )["episode_id"]
