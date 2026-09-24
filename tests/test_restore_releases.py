"""Compare actual immutable release source without executing historical code."""

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_implementation_fingerprint,
    source_files,
)
from balatro_horizons.evidence.reuse import revision_sources

PRE_RECOVERY = "5ad42191e760388e4ebb953bb13c2ebb84a48dae"
SINGLE_RESTORE = "2d31ce02b94df8f465b13724226acdc9bb11ee60"
RESTORE_RELEASE = "bb26f797b69ba42a32f6e0787e8d94b26ae9fadd"


@pytest.mark.parametrize("revision", [PRE_RECOVERY, SINGLE_RESTORE])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_actual_supported_release_source_passes_one_way_migration(monkeypatch, revision, game_kind):
    # CI fetches history so a missing supported release fails instead of skipping.
    # Preserve the supported 1x upgrade at its immutable target, not a later
    # release whose native runtime intentionally has a different identity.
    _, target = revision_sources(ROOT, RESTORE_RELEASE)
    target_hash = fingerprint_sources(target)
    monkeypatch.setattr(compatibility, "source_files", lambda root: target)
    monkeypatch.setattr(compatibility, "implementation_fingerprint", lambda: target_hash)
    commit, historical = revision_sources(ROOT, revision)
    source_hash = fingerprint_sources(historical)
    def resolve(root, expected):
        assert root == ROOT and expected == source_hash
        return commit
    monkeypatch.setattr(compatibility, "certified_revision", resolve)
    protocol = {"implementation_hash": source_hash}
    receipt = compatibility.prepare_compatibility({source_hash}, protocol, game_kind=game_kind)
    assert receipt["source_revisions"] == {source_hash: commit}
    assert receipt["accepted_implementation_hash"] == target_hash
    assert receipt["game_kind"] == game_kind
    compatibility.validate_compatibility(receipt)


def test_visible_speed_change_requires_a_new_native_fingerprint():
    _, previous = revision_sources(ROOT, RESTORE_RELEASE)
    previous_hash = native_implementation_fingerprint(previous)
    assert previous_hash == "a5662e2530340108571af4e1238f7a691b75dd0b75f9cb2deafd496e11ced3c0"
    assert native_implementation_fingerprint(source_files(ROOT)) != previous_hash


@pytest.mark.parametrize("revision", [PRE_RECOVERY, SINGLE_RESTORE, RESTORE_RELEASE])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_old_speed_profile_cannot_migrate_into_current_runtime(monkeypatch, revision, game_kind):
    commit, historical = revision_sources(ROOT, revision)
    source_hash = fingerprint_sources(historical)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *args: commit)
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.prepare_compatibility(
            {source_hash}, {"implementation_hash": source_hash}, game_kind=game_kind,
        )


def test_unreviewed_older_execution_contract_refuses(monkeypatch):
    revision, historical = revision_sources(ROOT, "487d3b0d1ef7ada22d425583fb49870ca654d2ca")
    source_hash = fingerprint_sources(historical)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *args: revision)
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.prepare_compatibility(
            {source_hash}, {"implementation_hash": source_hash}, game_kind="native",
        )
