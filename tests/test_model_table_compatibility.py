"""Runs recorded before the model tables restore only across that exact change."""

import io
import json
import os
import subprocess
import sys
import tarfile
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import (
    MODEL_TABLE_SOURCES,
    MODEL_TABLE_UPGRADES,
    NATIVE_GATE_SOURCE,
    NATIVE_GATE_UPGRADES,
)
from balatro_horizons.evidence.provenance import fingerprint_sources, native_component_manifest
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.storage.journal import digest

CERTIFIED = "68fe9fa25844eb23836292ab89de0cb594d4a1f2"
MAIN = "6b95c0c18d262512829a9e0c6fb4e82bd46defcd"
APPROVED_TARGET = "640a198a4ca3ce210c03377bfe92b1a256d1ba07"
PREFIX = "src/balatro_horizons/"
PROTOCOLS = {
    "anthropic": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
    "openai": {"provider": "openai", "model": "gpt-6-luna"},
    "baseline": None,
}


@pytest.fixture(params=[CERTIFIED, MAIN])
def deployed(request, monkeypatch):
    _, sources = revision_sources(ROOT, request.param)
    assert fingerprint_sources(sources) in MODEL_TABLE_SOURCES
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: request.param)
    return request.param, sources


@pytest.fixture
def approved_target(monkeypatch):
    commit, sources = revision_sources(ROOT, APPROVED_TARGET)
    assert commit == APPROVED_TARGET
    # Evaluate the immutable reviewed target without executing historical code.
    monkeypatch.setattr(compatibility, "source_files", lambda _: sources)
    return sources


def changes(before, after):
    return {
        name.removeprefix(PREFIX): (before.get(name), after.get(name))
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }


def test_both_deployed_sources_are_listed():
    sources = {
        fingerprint_sources(revision_sources(ROOT, commit)[1]) for commit in (CERTIFIED, MAIN)
    }
    assert sources == set(MODEL_TABLE_SOURCES)
    assert NATIVE_GATE_SOURCE in MODEL_TABLE_SOURCES


@pytest.mark.parametrize("provider", list(PROTOCOLS))
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_model_tables_are_the_only_execution_delta(deployed, approved_target, provider, game_kind):
    commit, sources = deployed
    source = fingerprint_sources(sources)
    before, after = execution_manifest(sources), execution_manifest(approved_target)
    expected = dict(MODEL_TABLE_UPGRADES)
    if source == NATIVE_GATE_SOURCE:
        expected.update(NATIVE_GATE_UPGRADES)
    assert changes(before, after) == expected
    assert native_component_manifest(sources) == native_component_manifest(approved_target)
    assert execution_manifest(sources, historical=True) == after
    protocol = {"implementation_hash": source, "model": PROTOCOLS[provider]}
    original = deepcopy(protocol)
    receipt = compatibility.prepare_compatibility({source}, protocol, game_kind=game_kind)
    assert protocol == original and receipt["protocol_hash"] == digest(original)
    assert receipt["source_revisions"] == {source: commit}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("path", list(MODEL_TABLE_UPGRADES))
@pytest.mark.parametrize("mutation", ["revert", "extra_statement", "missing"])
def test_unreviewed_target_cannot_inherit_model_table_approval(
    deployed,
    approved_target,
    monkeypatch,
    path,
    mutation,
):
    _, sources = deployed
    source = fingerprint_sources(sources)
    current = dict(approved_target)
    protocol = {"implementation_hash": source, "model": PROTOCOLS["anthropic"]}
    compatibility.prepare_compatibility({source}, protocol, game_kind="native")
    name = PREFIX + path
    if mutation == "revert":
        if name in sources:
            current[name] = sources[name]
        else:
            current.pop(name)
    elif mutation == "missing":
        current.pop(name)
    else:
        current[name] += b"\nUNREVIEWED_MODEL_TABLE_CHANGE = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({source}, protocol, game_kind="native")


def test_unknown_historical_fingerprint_cannot_use_model_table_mapping(
    deployed,
    approved_target,
    monkeypatch,
):
    commit, sources = deployed
    altered = {**sources, PREFIX + "config.py": sources[PREFIX + "config.py"] + b"\n# unlisted\n"}
    source = fingerprint_sources(altered)
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (commit, altered))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {source},
            {"implementation_hash": source},
            game_kind="native",
        )


# Previously declared models, each restored exactly as before the model tables.
DECLARED = [
    *(
        {"provider": "anthropic", "model": model}
        for model in (
            "claude-fable-5-1",
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
        )
    ),
    *(
        {"provider": "openai", "model": model}
        for model in (
            "gpt-6-sol",
            "gpt-6-luna",
            "gpt-6-terra",
            "gpt-6-astra",
            "gpt-5.6-luna",
            "gpt-5.6-terra",
            "gpt-5.6-sol",
            "gpt-5.6-astra",
        )
    ),
    None,
]
NEW_OPENAI = ["gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-terra", "gpt-6.1-sol"]
FABLE_5_2 = {"provider": "anthropic", "model": "claude-fable-5-2"}
# Builds each first request offline, with no client, for settings both sources accept.
FIRST_REQUESTS = """
import hashlib, json
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.contracts import RemainingBudget
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.observations.projection import HandleIssuer, project_public

view = project_public(FakeGame().observe_private(), episode_id="e" * 32, observation_id=0,
                      issuer=HandleIssuer(b"a" * 32), memory="",
                      remaining_budget=RemainingBudget(game_actions=1500, provider_calls=2000))
low = ["low", "medium", "high", "xhigh", "max"]
rows = {"gpt-6.2-astra": low, "gpt-6.2-sol": low, "gpt-6.1-sol": low,
        "gpt-6.1-terra": ["none", *low]}
out = {}
for model, efforts in rows.items():
    cases = [{}, *({"reasoning_effort": e} for e in efforts),
             *({"reasoning_effort": "medium", "reasoning_summary": s}
               for s in ("concise", "detailed"))]
    if "none" in efforts:
        cases.append({"reasoning_effort": "none", "temperature": 0.5})
    for settings in cases:
        config = ModelConfig(provider="openai", model=model, input_usd_per_million=1,
                             output_usd_per_million=2, cached_input_usd_per_million=0.1,
                             cache_write_input_usd_per_million=1.25,
                             pricing_date="2026-11-22", settings=settings)
        body = DirectProvider(config, Limits()).request(context(view), [])
        out[model + json.dumps(settings, sort_keys=True)] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
print(json.dumps(out, sort_keys=True))
"""


def admission(source, model, game_kind="native"):
    protocol = {"implementation_hash": source, "model": model}
    try:
        receipt = compatibility.prepare_compatibility({source}, protocol, game_kind=game_kind)
        compatibility.validate_compatibility(receipt)
    except ValueError as error:
        return str(error)
    return "admitted"


def first_requests(source_root):
    result = subprocess.run(
        [sys.executable, "-c", FIRST_REQUESTS],
        cwd=source_root,
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(source_root / "src")},
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_frozen_fable_5_2_run_is_refused_before_any_request(deployed, approved_target, game_kind):
    _, sources = deployed
    outcome = admission(fingerprint_sources(sources), FABLE_5_2, game_kind)
    assert outcome == "RESTORE_MODEL_REQUEST_CHANGED"


def test_execution_refuses_a_fable_5_2_receipt(deployed, monkeypatch):
    commit, sources = deployed
    bundle = {"implementation_hash": fingerprint_sources(sources), "model": FABLE_5_2}
    record = {
        "protocol_hash": digest(bundle),
        "preserved_provider": None,
        "source_revisions": {bundle["implementation_hash"]: commit},
    }
    monkeypatch.setattr(compatibility, "read_compatibility", lambda *_: record)
    try:
        compatibility.require_protocol_compatibility(None, {}, bundle)
        outcome = "admitted"
    except ValueError as error:
        outcome = str(error)
    assert outcome == "RESTORE_MODEL_REQUEST_CHANGED"


@pytest.mark.parametrize("model", DECLARED, ids=lambda model: model and model["model"])
def test_previously_declared_models_and_baselines_restore_as_before(
    deployed, approved_target, model
):
    _, sources = deployed
    assert admission(fingerprint_sources(sources), model) == "admitted"


@pytest.mark.parametrize("model", NEW_OPENAI)
def test_new_openai_runs_restore_because_their_requests_are_unchanged(
    deployed, approved_target, model
):
    _, sources = deployed
    assert admission(fingerprint_sources(sources), {"provider": "openai", "model": model}) == (
        "admitted"
    )


@pytest.mark.parametrize("commit", [CERTIFIED, MAIN])
def test_new_openai_first_requests_are_byte_identical_at_each_source(commit, tmp_path):
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", commit, "src", "configs"],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(tmp_path, filter="data")
    historical, current = first_requests(tmp_path), first_requests(ROOT)
    assert len(current) == 34
    assert historical == current
