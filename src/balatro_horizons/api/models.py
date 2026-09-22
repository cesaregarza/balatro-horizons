"""Validated request models for the HTTP boundary.

Keeping these models separate from route registration makes the application
factory small and gives the CLI and browser the same strict input contract.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(Input):
    agent: str = "heuristic"
    offline: bool = True
    calibration: bool = False
    seed: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    preset: Literal["pilot", "smoke"] = "pilot"


class OpenReview(Input):
    episode_id: str
    retrospective: bool = False
    prior_seed_exposure: bool = False


class SeekReview(Input):
    decision: int = Field(ge=0, strict=True)


class BranchInput(Input):
    episode_id: str
    decision: int = Field(ge=0)
    mode: Literal[
        "agent_continue", "single_action_override", "short_human_sequence", "human_takeover"
    ]
    actions: list[dict] = Field(default_factory=list, max_length=20)


class PanelInput(Input):
    count: int = Field(default=20, ge=1, le=200)


class BatchInput(Input):
    panel_id: str
    agents: list[str] = Field(min_length=1, max_length=10)
    replicates: int = Field(default=2, ge=1, le=10)


class BatchRun(Input):
    offline: bool = True


class VerifyInput(Input):
    mode: Literal["checkpoint", "seed_prefix", "checkpoint_probe"] = "checkpoint"
    episode_id: str
    decision: int = Field(ge=0)
    probe_action: dict | None = None


class BudgetContinuationInput(Input):
    combined_cap_usd: float = Field(gt=0, allow_inf_nan=False, strict=True)
    parent_terminal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SettingsInput(Input):
    skills: Literal["balatro-guide-v1", "none"] = "balatro-guide-v1"
    budgets: dict
    models: dict
