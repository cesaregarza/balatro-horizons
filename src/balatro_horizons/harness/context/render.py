"""The single live/frozen renderer for rules, tools, and prompt constants."""

import re

from balatro_horizons.config import NOTEBOOK_KEY_MAX, NOTEBOOK_KEY_MIN, RETAINED_HELPER_RESULTS
from balatro_horizons.harness.context.memory import notebook_tools
from balatro_horizons.harness.context.present import focused_tools
from balatro_horizons.harness.context.references import indexed_tools
from balatro_horizons.harness.skills import discovery
from balatro_horizons.harness.tool_interface import stable_tools

KERNEL = (
    "The objective is the ordinary Ante 8 native run win. Hand scores resolve in native "
    "order. Discards consume a discard; playing consumes a hand. Money, remaining hands, "
    "Jokers, consumables and their order carry native effects. Use only visible state and "
    "permitted history. Hidden identities and future draws are unknown. Rules lookup "
    "accepts a visible item name, a rules key, or index to list frozen keys."
)


def rules_kernel(skills):
    base = (
        "Resolve scores in native order. Read the available skills and linked rules when useful."
        if skills else KERNEL
    )
    return base + discovery(skills)


def tool_catalog(skills):
    return indexed_tools(notebook_tools(
        focused_tools(stable_tools(skills=skills, target_guidance=True)), action_notes=True
    ))


def render_prompt(raw: bytes) -> bytes:
    replacements = {
        b"{retained_helper_results}": str(RETAINED_HELPER_RESULTS).encode(),
        b"{notebook_key_min}": str(NOTEBOOK_KEY_MIN).encode(),
        b"{notebook_key_max}": str(NOTEBOOK_KEY_MAX).encode(),
    }
    for marker, value in replacements.items():
        raw = raw.replace(marker, value)
    return raw


LITERAL_NUMBER_PHRASES = (
    "Ante 8",  # The benchmark's ordinary win condition, not a configurable limit.
    "UTF-8",  # The protocol's encoding name.
    "offset 0",  # The first cursor in a paged read.
)


def validate_prompt_template(template: str) -> None:
    # The synced ALWAYS-LOADED block is authored and guarded separately.
    before, marker, remainder = template.partition("<!-- BEGIN ALWAYS-LOADED.md -->")
    if marker:
        _, _, after = remainder.partition("<!-- END ALWAYS-LOADED.md -->")
        template = before + after
    for marker in ("{retained_helper_results}", "{notebook_key_min}", "{notebook_key_max}"):
        if template.count(marker) != 1:
            raise ValueError("PROMPT_CONFIG_MARKER_MISSING")
    allowed = []
    for phrase in LITERAL_NUMBER_PHRASES:
        start = template.find(phrase)
        if start < 0 or template.find(phrase, start + 1) >= 0:
            raise ValueError("PROMPT_LITERAL_CHANGED")
        allowed.append((start, start + len(phrase)))
    for match in re.finditer(r"\d+", template):
        if not any(start <= match.start() and match.end() <= end for start, end in allowed):
            raise ValueError("PROMPT_NUMBER_NOT_CONFIGURED")
