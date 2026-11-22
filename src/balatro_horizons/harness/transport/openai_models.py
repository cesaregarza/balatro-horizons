"""Declared OpenAI models, read by the Responses rules, display names and presets.

Standard short-context prices (<=272K input) checked 2026-11-22, USD per million tokens:
https://developers.openai.com/api/docs/pricing
Efforts and sampling rules: https://developers.openai.com/api/docs/models/<model>
and https://developers.openai.com/api/docs/guides/latest-model
"""

from typing import NamedTuple

NONE_TO_MAX = ("none", "low", "medium", "high", "xhigh", "max")
LOW_TO_MAX = NONE_TO_MAX[1:]
MINIMAL = frozenset({"minimal"})
MINIMAL_OR_NONE = frozenset({"minimal", "none"})


class OpenAIModel(NamedTuple):
    name: str
    # Empty: offer only the effort the configuration pins.
    efforts: tuple = ()
    # Refused before a paid request.
    refused_efforts: frozenset = frozenset()
    # "any", "with none" (only at reasoning_effort none) or "never".
    temperature: str = "any"
    # Preset effort and (input, output, 30-minute cache write, cache read); None: no preset.
    preset: tuple | None = None


MODELS = {
    "gpt-6.2-astra": OpenAIModel(
        "GPT-6.2 Astra", LOW_TO_MAX, MINIMAL_OR_NONE, "never", ("medium", (8, 40, 10, 0.40))
    ),
    "gpt-6.2-sol": OpenAIModel(
        "GPT-6.2 Sol", LOW_TO_MAX, MINIMAL_OR_NONE, "never", ("medium", (2, 10, 2.5, 0.10))
    ),
    "gpt-6.1-terra": OpenAIModel(
        "GPT-6.1 Terra", NONE_TO_MAX, MINIMAL, "with none", ("medium", (1, 5, 1.25, 0.05))
    ),
    "gpt-6.1-sol": OpenAIModel(
        "GPT-6.1 Sol", LOW_TO_MAX, MINIMAL_OR_NONE, "never", ("medium", (2, 10, 2.5, 0.10))
    ),
    "gpt-6-sol": OpenAIModel(
        "GPT-6 Sol", NONE_TO_MAX, MINIMAL, "with none", ("medium", (2, 10, 2.5, 0.20))
    ),
    "gpt-6-luna": OpenAIModel(
        "GPT-6 Luna", NONE_TO_MAX, MINIMAL, "with none", ("medium", (0.10, 0.50, 0.125, 0.01))
    ),
    "gpt-6-terra": OpenAIModel("GPT-6 Terra"),
    "gpt-6-astra": OpenAIModel("GPT-6 Astra", LOW_TO_MAX),
    "gpt-5.6-luna": OpenAIModel("GPT-5.6 Luna", NONE_TO_MAX, MINIMAL),
    "gpt-5.6-terra": OpenAIModel("GPT-5.6 Terra", NONE_TO_MAX),
    "gpt-5.6-sol": OpenAIModel("GPT-5.6 Sol", NONE_TO_MAX),
    "gpt-5.6-astra": OpenAIModel("GPT-5.6 Astra", NONE_TO_MAX),
}
# Other IDs in these families, such as snapshots, keep their family's efforts.
FAMILY_EFFORTS = {"gpt-5.6": NONE_TO_MAX, "gpt-6-astra": LOW_TO_MAX}
UNDECLARED = OpenAIModel("")


def declared(model):
    """The model's table row; an undeclared ID has no refusals and no preset."""
    if model in MODELS:
        return MODELS[model]
    for family, efforts in FAMILY_EFFORTS.items():
        if model == family or model.startswith(family + "-"):
            return UNDECLARED._replace(efforts=efforts)
    return UNDECLARED
