"""Public run choices in both provider prefixes; synthetic/offline evidence only."""

import inspect
import json
from copy import deepcopy
from itertools import product
from pathlib import Path
from unittest.mock import Mock

import pytest
import test_campaign_budget
from test_boundary import project
from test_transport_parity import configured

from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import context, decision_context
from balatro_horizons.harness.context.freeze import (
    freeze_protocol,
    restore_protocol,
    validate_continuation,
)
from balatro_horizons.harness.context.render import rules_kernel
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.skills import prepare_rules
from balatro_horizons.harness.transport import DirectProvider, context_payload
from balatro_horizons.run_configuration import OPTIONS
from balatro_horizons.workbench.branches import prepare_branch


@pytest.mark.parametrize("deck,stake", product(OPTIONS["decks"], OPTIONS["stakes"]))
@pytest.mark.parametrize("skills", ["none", "balatro-guide-v1"])
def test_every_pair_reaches_both_provider_prefixes_before_first_decision(config, deck, stake, skills):
    config.environment.deck, config.environment.stake = deck, stake
    config.skills = skills
    rules = prepare_rules({}, config.skills)
    frozen = freeze_protocol(config, Baseline("heuristic"), rules)
    ctx = context(project(FakeGame().observe_private()), frozen=frozen)
    line = f"Run configuration: deck={deck}; stake={stake}."
    assert len(line.encode()) <= 64
    assert frozen["run_configuration"] == {"deck": deck, "stake": stake}
    assert frozen["rules_kernel"] == rules_kernel(rules.get("skills", [])) + "\n\n" + line
    openai = context_payload(ctx, [], "openai")
    anthropic = context_payload(ctx, [], "anthropic")
    prefix = openai["input"][0]["content"][0]
    assert prefix["text"] == anthropic["system"] == ctx.prompt + "\n\n" + frozen["rules_kernel"]
    assert prefix["text"].endswith(line) and prefix["text"].count(line) == 1
    assert prefix["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "Run configuration:" not in openai["input"][1]["content"]
    assert "Run configuration:" not in anthropic["messages"][0]["content"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_request_prefix_stays_stable_across_actions_helpers_and_default_changes(config, provider):
    config.environment.deck, config.environment.stake = "PLASMA", "ORANGE"
    frozen = freeze_protocol(config, Baseline("heuristic"), {})
    original = deepcopy(frozen)
    transport = DirectProvider(configured(provider), config.budgets)
    prefixes, dynamics = [], []
    exchange = {"operation": {"kind": "arithmetic", "expression": "2+2"}, "result": "4"}
    try:
        for index, phase in enumerate(("BLIND_SELECT", "SELECTING_HAND", "ROUND_EVAL", "SHOP")):
            game = FakeGame()
            game.phase = phase
            config.environment.deck, config.environment.stake = "RED", "WHITE"
            ctx, exchanges = decision_context(
                project(game.observe_private(), index=index), [exchange] if index else [],
                frozen=frozen,
            )
            body = transport.request(ctx, exchanges)
            prefixes.append(body["input"][0] if provider == "openai" else body["system"])
            dynamics.append(body["input"][1:] if provider == "openai" else body["messages"])
    finally:
        transport.client.close()
    assert all(prefix == prefixes[0] for prefix in prefixes)
    assert all(dynamic != dynamics[0] for dynamic in dynamics[1:])
    assert "Run configuration: deck=PLASMA; stake=ORANGE." in json.dumps(prefixes[0])
    assert frozen == original
    fresh = freeze_protocol(config, Baseline("heuristic"), {})
    assert fresh["run_configuration"] == {"deck": "RED", "stake": "WHITE"}
    assert fresh["rules_kernel"] != frozen["rules_kernel"]
    assert fresh["tool_catalog"] == frozen["tool_catalog"]
    assert fresh["prompt_utf8"] == frozen["prompt_utf8"]
    assert fresh["interface"] == frozen["interface"] == "tools_v8.5"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_pair_delivery_does_not_serialize_environment_private_state_or_hashes(config, provider, monkeypatch):
    config.environment.runtime = "/private/RUNTIME_PATH_SENTINEL"
    config.environment.powershell = "/private/SHELL_PATH_SENTINEL"
    config.environment.resolved_manifest = "/private/MANIFEST_PATH_SENTINEL"
    config.environment.unlock_profile = "PROFILE_SENTINEL"
    config.models["test"] = configured(provider)
    monkeypatch.setenv("OPENAI_API_KEY", "KEY_SENTINEL")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "KEY_SENTINEL")
    transport = DirectProvider(config.models["test"], config.budgets)
    try:
        frozen = freeze_protocol(config, transport, {})
        raw = FakeGame("SEED_SENTINEL").observe_private()
        raw["raw_engine"] = {"private": "RAW_ENGINE_SENTINEL"}
        ctx = context(project(raw), frozen=frozen)
        delivered = json.dumps(transport.request(ctx, []))
    finally:
        transport.client.close()
    assert "SENTINEL" not in delivered and "/private/" not in delivered
    assert "raw_engine" not in delivered
    for key in ("implementation_hash", "prompt_sha256", "knowledge_hash"):
        assert key not in delivered and frozen[key] not in delivered
    assert frozen["run_configuration"] == {"deck": "RED", "stake": "GOLD"}


def test_running_pair_and_branch_ignore_mutated_launch_defaults(store, config):
    config.environment.deck, config.environment.stake = "ERRATIC", "PURPLE"
    private = {"seed": "PRIVATE_PAIR_FIXTURE", "config": config.model_dump()}

    class RecordingBaseline(Baseline):
        def __init__(self):
            super().__init__("heuristic")
            self.contexts = []

        def decide(self, ctx, exchanges):
            self.contexts.append(deepcopy(ctx))
            config.environment.deck, config.environment.stake = "BLUE", "WHITE"
            return super().decide(ctx, exchanges)

    spending = Spending.episode_only(store.root / "private_runs/test-spending.json", 1)
    policy = RecordingBaseline()
    parent = Runner(store, config, FakeGame(), policy, spending).run(private=private)["episode_id"]
    assert len(policy.contexts) > 1
    path = store.episode_path(parent, True) / "agent-protocol.json"
    original = path.read_bytes()
    saved = Config.model_validate(store.manifest(parent, True)["config"])
    child, resume, prefix = prepare_branch(store, saved, parent, 0, "agent_continue")
    branch_policy = RecordingBaseline()
    result = Runner(store, saved, FakeGame(), branch_policy, spending).run(
        eid=child, resume=resume, history_prefix=prefix,
    )
    assert result["outcome"] == "WIN"
    assert path.read_bytes() == original
    assert (store.episode_path(child, True) / "agent-protocol.json").read_bytes() == original
    assert all(ctx.rules_kernel.endswith("Run configuration: deck=ERRATIC; stake=PURPLE.")
               for ctx in policy.contexts + branch_policy.contexts)


@pytest.mark.parametrize("choice,value", [("deck", "PLASMA"), ("stake", "WHITE")])
@pytest.mark.parametrize("human", [False, True])
def test_changed_pair_refuses_admission_and_direct_runner_continuations(
    store, config, episode, choice, value, human,
):
    checkpoint = read_checkpoint(store, episode, 0)
    frozen = restore_protocol(store, checkpoint)
    validate_continuation(frozen, config, "heuristic", human=human)
    changed = config.model_copy(deep=True)
    setattr(changed.environment, choice, value)
    count = len(store.list_episodes())
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_CONFIGURATION_CHANGED$"):
        validate_continuation(frozen, changed, "heuristic", human=human)
    mode = "human_takeover" if human else "agent_continue"
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_CONFIGURATION_CHANGED$"):
        prepare_branch(store, changed, episode, 0, mode)
    spending = Spending.episode_only(store.root / "private_runs/test-spending.json", 1)
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_CONFIGURATION_CHANGED$"):
        Runner(store, changed, FakeGame(), Baseline("heuristic"), spending).run(resume=checkpoint)
    assert len(store.list_episodes()) == count


@pytest.mark.parametrize("selection", [None, {}, {"deck": "RED"}, {"deck": "RED", "stake": None}])
def test_present_but_incomplete_pair_is_not_treated_as_legacy(config, selection):
    frozen = freeze_protocol(config, Baseline("heuristic"), {})
    frozen["run_configuration"] = selection
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_CONFIGURATION_CHANGED$"):
        validate_continuation(frozen, config, "heuristic")


def test_pair_tampering_cannot_reuse_the_checkpoint_protocol_reference(store, episode):
    checkpoint = read_checkpoint(store, episode, 0)
    path = store.episode_path(episode, True) / "agent-protocol.json"
    frozen = json.loads(path.read_text())
    frozen["run_configuration"]["deck"] = "PLASMA"
    path.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_SNAPSHOT_MISMATCH$"):
        restore_protocol(store, checkpoint)


UNKNOWN = [("deck", "UNKNOWN_DECK"), ("stake", "UNKNOWN_STAKE")]


@pytest.mark.parametrize("field, code", UNKNOWN)
def test_unknown_choice_is_refused_at_freeze(config, field, code):
    setattr(config.environment, field, "NOT A STANDARD " + field.upper())
    with pytest.raises(ValueError, match=f"^{code}$"):
        freeze_protocol(config, Baseline("heuristic"), {})


@pytest.mark.parametrize("field, code", UNKNOWN)
def test_direct_runner_refuses_an_unknown_choice_before_any_episode(store, config, field, code):
    setattr(config.environment, field, "NOT A STANDARD " + field.upper())
    runner = Runner(store, config, FakeGame(), Baseline("heuristic"),
                    Spending.episode_only(store.root / "private_runs" / "s.json", 1))
    with pytest.raises(ValueError, match=f"^{code}$"):
        runner.run()
    assert store.list_episodes() == []


@pytest.mark.parametrize("offline", [True, False], ids=["synthetic", "native"])
@pytest.mark.parametrize("field, code", UNKNOWN)
def test_service_start_refuses_an_unknown_choice_before_any_episode(
    store, config, monkeypatch, offline, field, code,
):
    from balatro_horizons import service
    from balatro_horizons.review.service import ReviewService

    session = Mock()
    monkeypatch.setattr(service, "load_session", session)
    native = Mock(side_effect=AssertionError("native game must never launch"))
    monkeypatch.setattr(service, "NativeGame", native)
    setattr(config.environment, field, "NOT A STANDARD " + field.upper())
    runs = service.RunService(store, ReviewService(store))
    try:
        with pytest.raises(ValueError, match=f"^{code}$"):
            runs.start(config, "heuristic", "SEED", offline=offline)
    finally:
        if runs.thread:
            runs.thread.join(30)
    assert store.list_episodes() == []
    session.assert_not_called()
    native.assert_not_called()


@pytest.mark.parametrize("field, code", UNKNOWN)
def test_service_execute_refuses_an_unknown_choice_before_any_episode(store, config, field, code):
    from balatro_horizons import service
    from balatro_horizons.review.service import ReviewService

    setattr(config.environment, field, "NOT A STANDARD " + field.upper())
    with pytest.raises(ValueError, match=f"^{code}$"):
        service.RunService(store, ReviewService(store)).execute(config, "heuristic", "SEED", offline=True)
    assert store.list_episodes() == []


def test_frozen_pair_is_inside_the_cached_anthropic_system_block(config):
    config.environment.deck, config.environment.stake = "PLASMA", "ORANGE"
    frozen = freeze_protocol(config, Baseline("heuristic"), {})
    model = configured("anthropic").model_copy(
        update={"cached_input_usd_per_million": 1, "cache_write_input_usd_per_million": 1})
    transport = DirectProvider(model, config.budgets)
    try:
        ctx, exchanges = decision_context(project(FakeGame().observe_private()), [], frozen=frozen)
        body = transport.request(ctx, exchanges)
    finally:
        transport.client.close()
    (block,) = body["system"]
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert block["text"].endswith("Run configuration: deck=PLASMA; stake=ORANGE.")
    assert "Run configuration:" not in json.dumps(body["messages"])


harness = test_campaign_budget.harness


@pytest.mark.parametrize("kind", ["deck", "stake"])
def test_selection_admission_is_inside_source_identity(harness, monkeypatch, kind):
    # Code that decides what a frozen run may contain must change the fingerprint when edited.
    from test_restore_unfinished import interrupted

    from balatro_horizons.config import ROOT
    from balatro_horizons.evidence import compatibility
    from balatro_horizons.evidence.provenance import fingerprint_sources, source_files
    from balatro_horizons.harness.context import freeze
    from balatro_horizons.service_restore import restore_preview

    parent = interrupted(harness)
    sources = source_files(ROOT)
    # Mutate the executable decision itself: the membership test the freeze path relies on.
    module = str(Path(inspect.getsourcefile(freeze.validate_choice)).relative_to(ROOT))
    membership = b'value not in OPTIONS[kind + "s"]'
    assert sources[module].count(membership) == 1
    bypass = membership + f' and kind != "{kind}"'.encode()
    mutant = {**sources, module: sources[module].replace(membership, bypass, 1)}
    assert fingerprint_sources(mutant) != fingerprint_sources(sources)
    # Run restore preview as the mutated source would: it must not call the parent same_source.
    monkeypatch.setattr(compatibility, "implementation_fingerprint", lambda: fingerprint_sources(mutant))
    monkeypatch.setattr(compatibility, "source_files", lambda _root: mutant)
    preview = restore_preview(harness.service(), parent, paid_enabled=True)
    assert not (preview["available"] and preview["plan"]["source_compatibility"] == "same_source")


@pytest.mark.parametrize("field, code", UNKNOWN)
def test_refused_start_leaves_service_status_unchanged(store, config, field, code):
    from balatro_horizons import service
    from balatro_horizons.review.service import ReviewService

    runs = service.RunService(store, ReviewService(store))
    runs.stop.set()
    runs.error = "REVIEW_PRIOR_FAILURE"
    setattr(config.environment, field, "NOT A STANDARD " + field.upper())
    with pytest.raises(ValueError, match=f"^{code}$"):
        runs.start(config, "heuristic", "SEED", offline=True)
    assert (runs.stop.is_set(), runs.error) == (True, "REVIEW_PRIOR_FAILURE")
    assert runs.thread is None


def test_accepted_start_still_clears_service_status(store, config):
    from balatro_horizons import service
    from balatro_horizons.review.service import ReviewService

    runs = service.RunService(store, ReviewService(store))
    runs.stop.set()
    runs.error = "REVIEW_PRIOR_FAILURE"
    runs.start(config, "heuristic", "SEED", offline=True)
    runs.thread.join(30)
    assert (runs.stop.is_set(), runs.error) == (False, None)
