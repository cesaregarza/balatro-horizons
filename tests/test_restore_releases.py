"""Compare actual immutable release source without executing historical code."""

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.provenance import fingerprint_sources, implementation_fingerprint
from balatro_horizons.evidence.reuse import revision_sources

PRE_RECOVERY = "5ad42191e760388e4ebb953bb13c2ebb84a48dae"
SINGLE_RESTORE = "2d31ce02b94df8f465b13724226acdc9bb11ee60"


@pytest.mark.parametrize("revision", [PRE_RECOVERY, SINGLE_RESTORE])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_actual_supported_release_source_passes_one_way_migration(monkeypatch, revision, game_kind):
    # CI fetches history so a missing supported release fails instead of skipping.
    commit, historical = revision_sources(ROOT, revision)
    source_hash = fingerprint_sources(historical)
    def resolve(root, expected):
        assert root == ROOT and expected == source_hash
        return commit
    monkeypatch.setattr(compatibility, "certified_revision", resolve)
    protocol = {"implementation_hash": source_hash}
    receipt = compatibility.prepare_compatibility({source_hash}, protocol, game_kind=game_kind)
    assert receipt["source_revisions"] == {source_hash: commit}
    assert receipt["accepted_implementation_hash"] == implementation_fingerprint()
    assert receipt["game_kind"] == game_kind
    compatibility.validate_compatibility(receipt)


def test_unreviewed_older_execution_contract_refuses(monkeypatch):
    revision, historical = revision_sources(ROOT, "487d3b0d1ef7ada22d425583fb49870ca654d2ca")
    source_hash = fingerprint_sources(historical)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *args: revision)
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.prepare_compatibility(
            {source_hash}, {"implementation_hash": source_hash}, game_kind="native",
        )
