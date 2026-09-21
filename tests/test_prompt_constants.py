"""Configured prompt numbers remain templates; only explained game literals stay fixed."""

import pytest

from balatro_horizons.config import (
    NOTEBOOK_KEY_MAX,
    NOTEBOOK_KEY_MIN,
    RETAINED_HELPER_RESULTS,
    ROOT,
)
from balatro_horizons.harness.context.render import render_prompt, validate_prompt_template


def test_prompt_constants_are_rendered_from_config():
    template = (ROOT / "configs/prompts/harness.txt").read_text()
    validate_prompt_template(template)
    rendered = render_prompt(template.encode()).decode()
    assert f"latest {RETAINED_HELPER_RESULTS} helper" in rendered
    assert f"({NOTEBOOK_KEY_MIN}-{NOTEBOOK_KEY_MAX} characters" in rendered
    assert "{retained_helper_results}" not in rendered


def test_typed_number_in_prompt_is_rejected():
    template = (ROOT / "configs/prompts/harness.txt").read_text()
    with pytest.raises(ValueError, match="PROMPT_CONFIG_MARKER_MISSING"):
        validate_prompt_template(template.replace("{retained_helper_results}", "3"))
    with pytest.raises(ValueError, match="PROMPT_NUMBER_NOT_CONFIGURED"):
        validate_prompt_template(template + "\nOnly 7 calls are allowed.\n")
