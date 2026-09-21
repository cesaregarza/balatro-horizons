"""The guide must remain portable and usable by the existing rules helper."""

import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from balatro_horizons.harness.contract import Rules
from balatro_horizons.harness.helpers import helper

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package_balatro_guide.py"
SPEC = importlib.util.spec_from_file_location("package_balatro_guide", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


@pytest.fixture
def guide(tmp_path):
    directory = tmp_path / "guide"
    skill = directory / "skills/balatro-orchestrator"
    skill.mkdir(parents=True)
    (directory / "README.md").write_text(
        "# Guide\n\n[Start](skills/balatro-orchestrator/SKILL.md)\n"
    )
    (directory / "KNOWN_LIMITS.md").write_text("# Limits\nUse observed counters.\n")
    (skill / "SKILL.md").write_text(
        "---\nname: balatro-orchestrator\ndescription: Route a game question.\n---\n"
        "# Start\nRead [limits](../../KNOWN_LIMITS.md) when a counter is unknown.\n"
    )
    return directory


def test_lookup_uses_existing_helper_without_filesystem_links(guide):
    _, rules, report = packaging.compile_guide(guide)
    result = helper(Rules(kind="rules", key="guide"), [], rules)
    assert "read_rules key: guide/limits" in result["entry"]
    assert ".md)" not in result["entry"]
    assert helper(Rules(kind="rules", key="guide/limits"), [], rules)["entry"].startswith(
        "# Limits"
    )
    assert report["lookup_entries"] == 2


def test_cli_packages_a_portable_deterministic_bundle(guide, tmp_path):
    archive_path = tmp_path / "guide.zip"
    report_path = tmp_path / "report.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--guide",
        str(guide),
        "--output",
        str(archive_path),
        "--report",
        str(report_path),
    ]
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    original = archive_path.read_bytes()
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    assert archive_path.read_bytes() == original
    report = json.loads(report_path.read_text())
    assert report["portable"] and report["origin_attribution_matches"] == 0
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        assert archive.testzip() is None
        rules = json.loads(archive.read("balatro-guide/rules.json"))
        assert rules == json.loads((guide / "rules.json").read_text())


@pytest.mark.parametrize("mutation", ["origin", "broken_link", "escape", "symlink"])
def test_rejects_unpublishable_guide(guide, tmp_path, mutation):
    limits = guide / "KNOWN_LIMITS.md"
    if mutation == "origin":
        limits.write_text("Advice from Fable and GPT.\n")
    elif mutation == "broken_link":
        limits.write_text("[Missing](missing.md)\n")
    elif mutation == "escape":
        outside = tmp_path / "outside.md"
        outside.write_text("Outside the guide.\n")
        limits.write_text("[Outside](../outside.md)\n")
    else:
        limits.unlink()
        limits.symlink_to(guide / "README.md")
    with pytest.raises(ValueError):
        packaging.compile_guide(guide)
