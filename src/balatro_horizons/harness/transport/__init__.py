"""Public provider transport seam and declared browser-visible capabilities."""

from urllib.parse import quote

from balatro_horizons.harness.transport import anthropic, openai
from balatro_horizons.harness.transport.anthropic import (
    CAPABILITIES as ANTHROPIC_CAPABILITIES,
)
from balatro_horizons.harness.transport.anthropic import ClaudeMessagesRuntime
from balatro_horizons.harness.transport.anthropic import public_capabilities as anthropic_public
from balatro_horizons.harness.transport.anthropic import validate_settings as anthropic_validate
from balatro_horizons.harness.transport.base import Transport
from balatro_horizons.harness.transport.conversation import canonical_messages
from balatro_horizons.harness.transport.errors import ProtocolFailure, ProviderFailure
from balatro_horizons.harness.transport.openai import CAPABILITIES as OPENAI_CAPABILITIES
from balatro_horizons.harness.transport.openai import OpenAIResponsesRuntime
from balatro_horizons.harness.transport.openai import public_capabilities as openai_public
from balatro_horizons.harness.transport.openai import validate_settings as openai_validate
from balatro_horizons.harness.transport.schema import validate_catalog

RUNTIMES = {"openai": OpenAIResponsesRuntime, "anthropic": ClaudeMessagesRuntime}
CAPABILITIES = {"openai": OPENAI_CAPABILITIES, "anthropic": ANTHROPIC_CAPABILITIES}
PUBLIC_CAPABILITIES = {"openai": openai_public, "anthropic": anthropic_public}


def runtime_type(provider):
    try:
        return RUNTIMES[provider]
    except KeyError:
        raise ValueError("unsupported provider") from None


def DirectProvider(model, limits, client=None):
    """Existing service construction surface, now selecting a native runtime."""
    return runtime_type(model.provider)(model, limits, client)


def context_payload(ctx, exchanges, provider):
    runtime_type(provider)
    return (openai if provider == "openai" else anthropic).payload(ctx, exchanges)


def tool_messages(ctx, exchanges, provider):
    return runtime_type(provider).tool_messages(ctx, exchanges)


def validate_runtime(model, limits, tools):
    """Pure local preflight consumed before RunService creates a game session."""
    validate_settings(model.provider, model.model, model.settings)
    validate_catalog(tools)
    (openai if model.provider == "openai" else anthropic).validate_config(model, limits)


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
    if provider == "openai":
        openai_validate(model, settings)
    else:
        anthropic_validate(model, settings)


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
    "validate_runtime",
]
