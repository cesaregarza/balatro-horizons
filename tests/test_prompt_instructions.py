import importlib.util
import shutil

import pytest
from test_boundary import project

from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.agents.frozen import freeze_protocol
from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import context_payload
from balatro_horizons.agents.skills import prepare_rules
from balatro_horizons.config import ROOT
from balatro_horizons.game.fake import FakeGame

SPEC = importlib.util.spec_from_file_location(
    "sync_prompt_instructions", ROOT / "scripts/sync_prompt_instructions.py"
)
sync_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_module)


def test_current_prompt_is_synced_and_delivered_to_both_providers(config):
    assert sync_module.sync(ROOT), "Run scripts/sync_prompt_instructions.py --write"
    config.skills = "balatro-guide-v1"
    rules = prepare_rules({}, config.skills)

    class Policy:
        pass

    frozen = freeze_protocol(config, Policy(), rules)
    instructions = (ROOT / "configs/prompts/ALWAYS-LOADED.md").read_text().strip()
    game = FakeGame()
    for phase in ("BLIND_SELECT", "SELECTING_HAND", "ROUND_EVAL", "SHOP"):
        game.phase = phase
        ctx, exchanges = decision_context(
            project(game.observe_private()), [], skills=rules["skills"], frozen=frozen
        )
        openai = context_payload(ctx, exchanges, "openai")
        anthropic = context_payload(ctx, exchanges, "anthropic")
        prefix = openai["input"][0]["content"][0]
        assert prefix["text"] == anthropic["system"]
        assert prefix["text"].count(instructions) == 1
        assert prefix["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert instructions in frozen["prompt_utf8"]


def test_sync_is_explicit_preserves_other_text_and_rejects_malformed_blocks(tmp_path):
    prompts = tmp_path / "configs/prompts"
    prompts.mkdir(parents=True)
    source, target = prompts / "ALWAYS-LOADED.md", prompts / "harness.txt"
    source.write_text("Original instructions.\n")
    target.write_text("Harness instructions.\n")
    assert not sync_module.sync(tmp_path)
    assert target.read_text() == "Harness instructions.\n"
    assert sync_module.sync(tmp_path, write=True)
    assert sync_module.sync(tmp_path)
    source.write_text("Revised instructions.\n")
    assert not sync_module.sync(tmp_path)
    assert sync_module.sync(tmp_path, write=True)
    assert "Original instructions." not in target.read_text()
    assert target.read_text().startswith("Harness instructions.\n\n")
    malformed = target.read_text().replace(sync_module.END, "")
    target.write_text(malformed)
    with pytest.raises(ValueError, match="complete generated block"):
        sync_module.sync(tmp_path, write=True)
    assert target.read_text() == malformed
    assert {path.name for path in prompts.iterdir()} == {source.name, target.name}


def test_instructions_remain_short():
    with pytest.raises(ValueError, match="1-1024"):
        sync_module.render("Prompt", "é" * 513)


def test_new_run_rejects_unsynced_instructions_before_game_start(
    config, store, tmp_path, monkeypatch
):
    from balatro_horizons.runner import Runner
    prompts = tmp_path / "source/configs/prompts"
    shutil.copytree(ROOT / "configs/prompts", prompts)
    monkeypatch.setattr("balatro_horizons.agents.frozen.ROOT", prompts.parents[1])
    class Policy:
        paid = False
    class UnstartedGame:
        evidence_kind = "fixture"
        def start(self, *args):
            pytest.fail("stale prompt must be rejected before native start")
    source = prompts / "ALWAYS-LOADED.md"
    source.write_text("Updated ordinary mechanic.\n")
    with pytest.raises(HarnessFailure, match="PERSISTENT_INSTRUCTIONS_STALE"):
        Runner(store, config, UnstartedGame(), Policy()).run()
    assert store.list_episodes() == []
    sync_module.sync(prompts.parents[1], write=True)
    frozen = freeze_protocol(config, Policy(), {})
    assert source.read_text().strip() in frozen["prompt_utf8"]
    source.write_text("too long" * 1024)
    with pytest.raises(HarnessFailure, match="PERSISTENT_INSTRUCTIONS_INVALID"):
        freeze_protocol(config, Policy(), {})


def test_service_rejects_stale_prompt_before_constructing_native_game(store, config, tmp_path, monkeypatch):
    from unittest.mock import Mock

    from test_provider_continuations import model

    from balatro_horizons.review.service import ReviewService
    from balatro_horizons.service import RunService
    prompts = tmp_path / "service/configs/prompts"
    shutil.copytree(ROOT / "configs/prompts", prompts)
    (prompts / "ALWAYS-LOADED.md").write_text("Source changed without syncing.\n")
    monkeypatch.setattr("balatro_horizons.service.ROOT", prompts.parents[1])
    native = Mock(side_effect=AssertionError("must not construct native game"))
    monkeypatch.setattr("balatro_horizons.service.NativeGame", native)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    config.models["test"] = model("openai")
    config.budgets.paid_calls_enabled = True
    config.budgets.max_episode_cost_usd = 1
    config.budgets.max_batch_cost_usd = 1
    service = RunService(store, ReviewService(store))
    for invoke in (service.start, service.execute):
        with pytest.raises(HarnessFailure, match="PERSISTENT_INSTRUCTIONS_STALE"):
            invoke(config, "test", "FIXTURE")
    native.assert_not_called()
    assert store.list_episodes() == []
