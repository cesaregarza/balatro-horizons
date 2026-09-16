import importlib.util

import pytest
from test_boundary import project

from balatro_horizons.agents.frozen import freeze_protocol
from balatro_horizons.agents.protocol import decision_context
from balatro_horizons.agents.providers import context_payload
from balatro_horizons.agents.skills import prepare_rules
from balatro_horizons.config import ROOT
from balatro_horizons.engine.fake import FakeGame

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
        interface = "tools_v5"

    frozen = freeze_protocol(config, Policy(), rules)
    instructions = (ROOT / "configs/prompts/ALWAYS-LOADED.md").read_text().strip()
    game = FakeGame()
    for phase in ("BLIND_SELECT", "SELECTING_HAND", "ROUND_EVAL", "SHOP"):
        game.phase = phase
        ctx, exchanges = decision_context(
            project(game.observe_private()), [], interface="tools_v5",
            skills=rules["skills"], frozen=frozen,
        )
        openai = context_payload(ctx, exchanges, "openai", "tools_v5")
        anthropic = context_payload(ctx, exchanges, "anthropic", "tools_v5")
        prefix = openai["input"][0]["content"][0]
        assert prefix["text"] == anthropic["system"]
        assert prefix["text"].count(instructions) == 1
        assert prefix["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert instructions in frozen["prompt_utf8"]


def test_sync_is_explicit_preserves_other_text_and_rejects_malformed_blocks(tmp_path):
    prompts = tmp_path / "configs/prompts"
    prompts.mkdir(parents=True)
    source, target = prompts / "ALWAYS-LOADED.md", prompts / "tools-v5.txt"
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
