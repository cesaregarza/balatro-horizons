"""Opt-in Claude presets, consumed by transport capabilities and Settings.

Standard global API prices checked 2026-10-08 (USD per million tokens):
https://platform.claude.com/docs/en/about-claude/pricing
Model IDs and adaptive-thinking defaults:
https://platform.claude.com/docs/en/models/overview
"""

EFFORTS = ("low", "medium", "high", "xhigh", "max")
HAIKU_INPUT_CEILING = 100_000
# Input, output, 5-minute cache write, cache read. Haiku uses its <=100k tier.
MODELS = {
    "claude-fable-5-1": ("Claude Fable 5.1", "high", (10, 50, 12.5, 0.25)),
    "claude-opus-5-5": ("Claude Opus 5.5", "medium", (4, 20, 5, 0.20)),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5", "high", (2, 10, 2.5, 0.10)),
    "claude-haiku-5-5": ("Claude Haiku 5.5", "medium", (0.10, 0.50, 0.125, 0.01)),
}


def presets():
    # Return fresh dictionaries: choosing a preset must not mutate saved defaults.
    return {
        name: {
            "provider": "anthropic",
            "model": model,
            "input_usd_per_million": rates[0],
            "output_usd_per_million": rates[1],
            "cache_write_input_usd_per_million": rates[2],
            "cached_input_usd_per_million": rates[3],
            "pricing_date": "2026-10-08",
            "settings": {"reasoning_effort": effort},
        }
        for model, (name, effort, rates) in MODELS.items()
    }
