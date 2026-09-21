"""Shared harness defaults and validated operator overrides.

Edit defaults here, then restart with matching certification. Episode YAML and
saved operator settings override model defaults; existing runs freeze their
resolved configuration. Never pass the whole operator config to a player.
"""

import math
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from balatro_horizons.game.environment import Environment

ROOT = Path(__file__).resolve().parents[2]

# Transport bytes, model tokens, and dollar reservations have independent units.
DEFAULT_INPUT_TOKEN_LIMIT = 32_768
DEFAULT_REQUEST_BYTE_LIMIT = 262_144
INPUT_TOKEN_SAFETY_MARGIN = 512
CONTEXT_FRAMING_BYTES = 4_096
CONTEXT_SETTINGS_BYTES = 1_024

# Public context and retrieval. These are protocol defaults, not game mechanics.
RECENT_PUBLIC_EVENT_LIMIT = 20
AUTOMATIC_PUBLIC_EVENT_COUNT = 2
EVENT_SUMMARY_CHARACTERS = 240
HELPER_PAGE_BYTES = 2_048
RETAINED_HELPER_RESULTS = 3
NOTEBOOK_KEY_MIN = 1
NOTEBOOK_KEY_MAX = 64
WORKING_MEMORY_DECISIONS = 3
WORKING_MEMORY_BYTES = 24_576
WORKING_MEMORY_HELPER_BYTES = 4_096
DEFAULT_GUIDE_PAGE_BYTES = 4_096
DEFAULT_HISTORY_PAGE_EVENTS = 10
MAX_HISTORY_PAGE_EVENTS = 20
SKILL_DESCRIPTION_PREVIEW_CHARACTERS = 160
ALWAYS_LOADED_MAX_BYTES = 1_024

# Agent-authored fields: keep tool schemas and action validation in agreement.
MAX_MEMORY_CHARACTERS = 4_096
MAX_DECISION_NOTE_CHARACTERS = 512
MAX_ARITHMETIC_CHARACTERS = 256
MAX_ABORT_REASON_CHARACTERS = 256
MAX_ARITHMETIC_NODES = 64

# Provider transport is independent of the Windows game bridge's timeout below.
PROVIDER_TIMEOUT_SECONDS = 90


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Limits(Options):
    max_game_actions: int = Field(default=1500, ge=1)
    max_provider_calls: int = Field(default=2000, ge=1)
    max_helper_calls_per_decision: int = Field(default=8, ge=0)
    max_input_tokens_per_call: int = Field(default=DEFAULT_INPUT_TOKEN_LIMIT, ge=128)
    max_request_bytes: int = Field(default=DEFAULT_REQUEST_BYTE_LIMIT, ge=1024)
    max_output_tokens_per_call: int = Field(default=8192, ge=64)
    max_consecutive_invalid_actions: int = Field(default=3, ge=1)
    max_transport_attempts: int = Field(default=3, ge=1, le=3)
    memory_max_characters: int = Field(
        default=MAX_MEMORY_CHARACTERS, ge=0, le=MAX_MEMORY_CHARACTERS
    )
    paid_calls_enabled: bool = False
    max_episode_cost_usd: float | None = Field(default=None, gt=0)
    max_batch_cost_usd: float | None = Field(default=None, gt=0)


class ModelConfig(Options):
    provider: Literal["openai", "anthropic"]
    model: str = Field(min_length=1)
    input_usd_per_million: float = Field(gt=0)
    output_usd_per_million: float = Field(gt=0)
    cached_input_usd_per_million: float | None = Field(default=None, gt=0)
    cache_write_input_usd_per_million: float | None = Field(default=None, gt=0)
    pricing_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    settings: dict = Field(default_factory=dict)

    @property
    def maximum_input_usd_per_million(self):
        return max(
            self.input_usd_per_million,
            self.cached_input_usd_per_million or 0,
            self.cache_write_input_usd_per_million or 0,
        )

    @model_validator(mode="after")
    def supported(self):
        if (self.cached_input_usd_per_million is None) != (
            self.cache_write_input_usd_per_million is None
        ):
            raise ValueError("configure both cache read and write rates")
        if self.cached_input_usd_per_million is not None and self.provider != "openai":
            raise ValueError("category cache pricing is currently OpenAI-only")
        from balatro_horizons.harness.transport import validate_settings

        validate_settings(self.provider, self.model, self.settings)
        date.fromisoformat(self.pricing_date)
        if self.model != self.model.strip() or not self.model.strip():
            raise ValueError("explicit model identifier required")
        temperature = self.settings.get("temperature")
        if temperature is not None and (
            type(temperature) not in (int, float)
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise ValueError("invalid temperature")
        if "reasoning_effort" in self.settings and self.settings["reasoning_effort"] not in (
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError("invalid reasoning effort")
        if "reasoning_summary" in self.settings and self.settings["reasoning_summary"] not in (
            "auto",
            "concise",
            "detailed",
        ):
            raise ValueError("invalid reasoning summary")
        if "thinking_budget" in self.settings and (
            type(self.settings["thinking_budget"]) is not int
            or self.settings["thinking_budget"] < 1024
        ):
            raise ValueError("invalid thinking budget")
        if self.provider == "anthropic" and "thinking_budget" in self.settings:
            if "temperature" in self.settings:
                raise ValueError("temperature is incompatible with manual thinking")
        return self


class Config(Options):
    # Review sessions and intervention controls are an explicit operator
    # opt-in. The ordinary dashboard remains enabled.
    workbench_enabled: bool = False
    skills: Literal["balatro-guide-v1", "none"] = "balatro-guide-v1"
    benchmark: dict = Field(
        default_factory=lambda: {
            "name": "balatro-horizons",
            "protocol_version": "0.1",
            "track": "structured-core",
            "objective": "native_run_win",
            "target_ante": 8,
        }
    )
    environment: Environment = Field(default_factory=Environment)
    sampling: dict = Field(default_factory=dict)
    budgets: Limits = Field(default_factory=Limits)
    execution: dict = Field(default_factory=lambda: {"workers": 1})
    models: dict[str, ModelConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fixed_protocol(self):
        if self.execution != {"workers": 1}:
            raise ValueError("this release supports exactly one worker")
        if (
            self.benchmark.get("objective") != "native_run_win"
            or self.benchmark.get("target_ante") != 8
        ):
            raise ValueError("this protocol targets the native Ante 8 win event")
        return self

    def public(self):
        from balatro_horizons.harness.transport import public_capability_table

        return {
            "benchmark": self.benchmark,
            "workbench": self.workbench_enabled,
            "skills": self.skills,
            "deck": self.environment.deck,
            "stake": self.environment.stake,
            "budgets": self.budgets.model_dump(),
            "models": {k: v.model_dump() for k, v in self.models.items()},
            "model_capabilities": public_capability_table(self.models),
        }


def load_config(path=None):
    return Config.model_validate(
        yaml.safe_load(Path(path or ROOT / "configs/pilot.yaml").read_text())
    )
