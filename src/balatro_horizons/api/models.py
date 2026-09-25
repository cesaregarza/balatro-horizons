"""Validated request models for the HTTP boundary.

Keeping these models separate from route registration makes the application
factory small and gives the CLI and browser the same strict input contract.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from balatro_horizons.cost_limits import DollarCap


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(Input):
    agent: str = "heuristic"
    offline: bool = True
    calibration: bool = False
    seed: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    preset: Literal["pilot", "smoke"] = "pilot"
    cost_override: Literal[10, "uncapped"] | None = None
    confirm_uncapped: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def confirmed_cost_override(self):
        if self.cost_override == "uncapped" and not self.confirm_uncapped:
            raise ValueError("UNCAPPED_CONFIRMATION_REQUIRED")
        return self


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
    combined_cap_usd: DollarCap | None = None
    additional_cost_usd: Literal[10] | None = None
    parent_terminal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    authorize_paid: bool = Field(default=False, strict=True)
    confirm_uncapped: bool = Field(default=False, strict=True)
    accept_compatible_update: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def explicit_funding(self):
        if (self.combined_cap_usd is None) == (self.additional_cost_usd is None):
            raise ValueError("ONE_BUDGET_OVERRIDE_REQUIRED")
        if self.additional_cost_usd is not None or self.combined_cap_usd == "uncapped":
            if not self.plan_hash or not self.authorize_paid:
                raise ValueError("BUDGET_PLAN_AUTHORIZATION_REQUIRED")
        if self.combined_cap_usd == "uncapped" and not self.confirm_uncapped:
            raise ValueError("UNCAPPED_CONFIRMATION_REQUIRED")
        return self


class RestoreInput(Input):
    parent_head: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorize_paid: bool = Field(default=False, strict=True)
    accept_compatible_update: bool = Field(default=False, strict=True)
    confirm_uncapped: bool = Field(default=False, strict=True)


class SettingsInput(Input):
    skills: Literal["balatro-guide-v1", "none"] = "balatro-guide-v1"
    budgets: dict
    models: dict
