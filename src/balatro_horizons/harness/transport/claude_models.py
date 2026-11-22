"""Declared Claude models, read by the Anthropic rules, display names and presets.

Standard global API prices checked 2026-11-22 (USD per million tokens):
https://platform.claude.com/docs/en/about-claude/pricing
Model IDs and adaptive-thinking defaults:
https://platform.claude.com/docs/en/models/overview
"""

from typing import NamedTuple

EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ClaudeModel(NamedTuple):
    name: str
    default_effort: str
    # Input, output, 5-minute cache write, cache read.
    rates: tuple
    # Larger inputs are refused: their long-context price is not configured.
    input_ceiling: int | None = None


MODELS = {
    "claude-fable-5-2": ClaudeModel("Claude Fable 5.2", "high", (8, 40, 10, 0.20)),
    "claude-fable-5-1": ClaudeModel("Claude Fable 5.1", "high", (10, 50, 12.5, 0.25)),
    "claude-opus-5-5": ClaudeModel("Claude Opus 5.5", "medium", (4, 20, 5, 0.20)),
    "claude-sonnet-5-5": ClaudeModel("Claude Sonnet 5.5", "high", (2, 10, 2.5, 0.10)),
    # Haiku's prices are its <=100k-input tier.
    "claude-haiku-5-5": ClaudeModel(
        "Claude Haiku 5.5", "medium", (0.10, 0.50, 0.125, 0.01), input_ceiling=100_000
    ),
}


def input_ceiling(model):
    return MODELS[model].input_ceiling if model in MODELS else None
