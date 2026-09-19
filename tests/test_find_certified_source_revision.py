"""Revision matching is read-only and fails closed on bad or absent matches."""

import importlib.util

import pytest

from balatro_horizons.config import ROOT


@pytest.fixture
def finder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "find_certified_source_revision", ROOT / "scripts/find_certified_source_revision.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matches_only_exact_fingerprint_and_preserves_revision_order(finder, monkeypatch):
    revisions = ["b" * 40, "a" * 40, "c" * 40]

    def check_output(command, *, text):
        assert command[-2:] == ["--", "src/balatro_horizons"]
        assert text is True
        return "\n".join(revisions) + "\n"

    monkeypatch.setattr(finder.subprocess, "check_output", check_output)
    monkeypatch.setattr(finder, "revision_sources", lambda root, rev: (rev, {"rev": rev}))
    monkeypatch.setattr(finder, "fingerprint_sources", lambda source: source["rev"][0] * 64)
    visited, matches = finder.matching_revisions(ROOT, "a" * 64)
    assert visited == revisions
    assert matches == ["a" * 40]


def test_cli_rejects_malformed_fingerprint_and_no_match(finder, monkeypatch, capsys):
    with pytest.raises(SystemExit, match="2"):
        finder.main(["--root", str(ROOT), "--fingerprint", "BAD"])
    monkeypatch.setattr(finder, "matching_revisions", lambda root, accepted: (["a" * 40], []))
    assert finder.main(["--root", str(ROOT), "--fingerprint", "a" * 64]) == 1
    assert '"matches": []' in capsys.readouterr().out
