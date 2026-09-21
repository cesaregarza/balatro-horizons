"""Public provider transport seam and declared browser-visible capabilities."""

from urllib.parse import quote

from balatro_horizons.harness.transport.anthropic import (
    CAPABILITIES as ANTHROPIC_CAPABILITIES,
)
from balatro_horizons.harness.transport.anthropic import SPEC as ANTHROPIC_SPEC
from balatro_horizons.harness.transport.anthropic import public_capabilities as anthropic_public
from balatro_horizons.harness.transport.base import (
    ProtocolFailure,
    ProviderFailure,
    Transport,
    canonical_messages,
    context_payload,
    tool_messages,
)
from balatro_horizons.harness.transport.openai import CAPABILITIES as OPENAI_CAPABILITIES
from balatro_horizons.harness.transport.openai import SPEC as OPENAI_SPEC
from balatro_horizons.harness.transport.openai import public_capabilities as openai_public

SPECS = {"openai": OPENAI_SPEC, "anthropic": ANTHROPIC_SPEC}
CAPABILITIES = {"openai": OPENAI_CAPABILITIES, "anthropic": ANTHROPIC_CAPABILITIES}
PUBLIC_CAPABILITIES = {"openai": openai_public, "anthropic": anthropic_public}
DirectProvider = Transport


def provider_spec(provider):
    try:
        return SPECS[provider]
    except KeyError:
        raise ValueError("unsupported provider") from None


def model_key(provider, model):
    return f"model:{provider}:{quote(model, safe='')}"


def validate_settings(provider, model, settings):
    try:
        capabilities = CAPABILITIES[provider]
    except KeyError:
        raise ValueError("unsupported provider") from None
    if set(settings) - capabilities["supported_settings"]:
        raise ValueError("unsupported provider setting")
    for key, values in capabilities["unsupported_settings"](model).items():
        if settings.get(key) in values:
            raise ValueError(f"{model} does not support {settings[key]} {key}")


def public_capability_table(models):
    providers = {
        name: {"supported_settings": sorted(table["supported_settings"])}
        for name, table in CAPABILITIES.items()
    }
    declared = {
        model_key(model.provider, model.model): PUBLIC_CAPABILITIES[model.provider](
            model.model, model.settings
        )
        for model in models.values()
    }
    return {"providers": providers, "models": declared}


__all__ = [
    "DirectProvider",
    "ProtocolFailure",
    "ProviderFailure",
    "Transport",
    "canonical_messages",
    "context_payload",
    "public_capability_table",
    "tool_messages",
    "validate_settings",
]
