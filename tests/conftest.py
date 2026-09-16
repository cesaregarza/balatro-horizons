import pytest

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.config import Config
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.runner import Runner
from balatro_horizons.storage.journal import Store


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def episode(store, config):
    private = {"seed": "DO_NOT_EXPORT_THIS_SEED", "config": config.model_dump()}
    return Runner(store, config, FakeGame(private["seed"]), Baseline("heuristic")).run(
        private=private
    )["episode_id"]
