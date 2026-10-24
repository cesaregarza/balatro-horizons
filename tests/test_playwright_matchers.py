"""Every name in a Playwright project's testMatch must select a spec that exists."""

import re

from balatro_horizons.config import ROOT


def _alternatives(group):
    """Split a regex group body on top-level '|' only."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(group):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(group[start:index])
            start = index + 1
    return [*parts, group[start:]]


def test_every_test_match_name_selects_an_existing_spec():
    config = (ROOT / "web/playwright.config.ts").read_text()
    specs = sorted(path.name for path in (ROOT / "web/tests").glob("*.spec.ts"))
    groups = re.findall(r"testMatch: /\(\?:(.*)\)\\\.spec\\\.ts/,", config)
    assert len(groups) == 2
    unmatched = [
        name for group in groups for name in _alternatives(group)
        if not any(re.fullmatch(rf"(?:{name})\.spec\.ts", spec) for spec in specs)
    ]
    assert unmatched == []
