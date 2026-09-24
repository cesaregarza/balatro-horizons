"""Identity coverage and one-way migrations are independently mutation-pinned."""

from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.compatibility import _identity
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import (
    MODULE_UPGRADES,
    SOURCE_ADDITIONS,
    upgrade_historical_manifest,
)
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    native_implementation_fingerprint,
    source_files,
)
from balatro_horizons.storage.journal import digest

PREFIX = "src/balatro_horizons/"


def test_every_fingerprinted_file_has_an_explicit_comparison_or_exclusion():
    sources = source_files(ROOT)
    fingerprinted = {name for name, content in sources.items()
                     if fingerprint_sources({name: content}) != digest({})}
    # Independent inventory: removing any file/set from execution_manifest fails.
    excluded = {PREFIX + path for path in (
        "game/fake.py", "evidence/compatibility.py", "evidence/execution_identity.py",
        "evidence/identity_migrations.py",
    )}
    native = set(native_component_manifest(sources))
    execution = set(execution_manifest(sources))
    assert not (execution & (native | excluded))
    assert execution | native | excluded == fingerprinted


def test_catalogue_targets_are_exact_current_module_asts():
    current = execution_manifest(source_files(ROOT))
    for path, (previous, accepted) in MODULE_UPGRADES.items():
        assert previous and accepted not in previous
        assert current[PREFIX + path] == accepted, path
        for historical in previous:
            manifest = {PREFIX + path: historical}
            assert upgrade_historical_manifest(manifest, "unlisted") == {PREFIX + path: accepted}
            assert manifest[PREFIX + path] == historical
        mutated = {PREFIX + path: digest("unreviewed behavioral edit")}
        assert upgrade_historical_manifest(mutated, "unlisted") == mutated
    for additions in SOURCE_ADDITIONS.values():
        for path, accepted in additions.items():
            assert current[PREFIX + path] == accepted, path
    assert upgrade_historical_manifest({}, "unlisted") == {}


def test_calibration_migration_is_one_way_and_never_normalizes_current_regression():
    current = source_files(ROOT)
    older = deepcopy(current)
    path = PREFIX + "service_execution.py"
    assert b"calibration=calibration," in older[path]
    older[path] = older[path].replace(
        b"calibration=calibration,", b"calibration=calibration or bool(request.resume),",
    )
    assert execution_manifest(older, historical=True) == execution_manifest(current)
    assert execution_manifest(older) != execution_manifest(current, historical=True)


def test_synthetic_identity_includes_fake_game_without_changing_native_contract():
    sources = source_files(ROOT)
    changed = deepcopy(sources)
    path = PREFIX + "game/fake.py"
    changed[path] = changed[path].replace(b"self.money += 4", b"self.money += 5")
    assert changed != sources
    assert _identity(sources, game_kind="synthetic") != _identity(changed, game_kind="synthetic")
    assert _identity(sources, game_kind="native") == _identity(changed, game_kind="native")
    assert native_implementation_fingerprint(sources) == native_implementation_fingerprint(changed)


@pytest.mark.parametrize("path", [
    "harness/decision.py", "harness/context/build.py", "harness/transport/openai.py",
    "harness/money.py", "service.py", "service_execution.py", "workbench/branches.py",
    "workbench/budget_ledger.py", "workbench/budget_continuation.py", "evidence/recovery.py",
    "evidence/provenance.py", "evidence/certification.py", "evidence/lock.py",
    "workbench/restoration.py", "workbench/restore_ledger.py", "service_restore.py",
])
def test_executable_edits_are_not_hidden_by_module_migrations(path):
    sources = source_files(ROOT)
    altered = {**sources, PREFIX + path: sources[PREFIX + path] + b"\nEXECUTABLE_MUTATION = True\n"}
    assert _identity(sources, game_kind="synthetic") != _identity(
        altered, game_kind="synthetic", historical=True,
    )
