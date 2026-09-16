"""Operator configuration. Never pass it wholesale to a playing policy."""

import math
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Limits(Options):
    max_game_actions: int = Field(default=1500, ge=1)
    max_provider_calls: int = Field(default=2000, ge=1)
    max_helper_calls_per_decision: int = Field(default=8, ge=0)
    max_input_tokens_per_call: int = Field(default=32768, ge=128)
    max_output_tokens_per_call: int = Field(default=8192, ge=64)
    max_consecutive_invalid_actions: int = Field(default=3, ge=1)
    max_transport_attempts: int = Field(default=3, ge=1, le=3)
    memory_max_characters: int = Field(default=4096, ge=0, le=4096)
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
        allowed = (
            {"temperature", "reasoning_effort", "reasoning_summary"}
            if self.provider == "openai"
            else {"temperature", "thinking_budget"}
        )
        allowed.add("harness_interface")
        if set(self.settings) - allowed:
            raise ValueError("unsupported provider setting")
        if self.settings.get("harness_interface", "operate_v1") not in (
            "operate_v1",
            "tools_v2",
            "tools_v3",
            "tools_v4",
            "tools_v5",
        ):
            raise ValueError("unsupported harness interface")
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
        if self.provider == "openai" and self.model == "gpt-5.6-luna":
            if self.settings.get("reasoning_effort") == "minimal":
                raise ValueError("Luna does not support minimal reasoning effort")
        if "thinking_budget" in self.settings and (
            type(self.settings["thinking_budget"]) is not int
            or self.settings["thinking_budget"] < 1024
        ):
            raise ValueError("invalid thinking budget")
        return self


class Environment(Options):
    adapter: str = "balatrobot"
    deck: str = "RED"
    stake: str = "GOLD"
    unlock_profile: str = "dedicated_fully_unlocked"
    resolved_manifest: str = "private/environment.lock.json"
    require_live_certification: Literal[True] = True
    runtime: str = "/mnt/d/BalatroHorizonsRuntime"
    powershell: str = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    port: int = Field(default=12346, ge=1024, le=65535)
    timeout_seconds: int = Field(default=90, ge=1, le=300)


class Config(Options):
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
        return {
            "benchmark": self.benchmark,
            "skills": self.skills,
            "deck": self.environment.deck,
            "stake": self.environment.stake,
            "budgets": self.budgets.model_dump(),
            "models": {k: v.model_dump() for k, v in self.models.items()},
        }


def load_config(path=None):
    return Config.model_validate(
        yaml.safe_load(Path(path or ROOT / "configs/pilot.yaml").read_text())
    )
