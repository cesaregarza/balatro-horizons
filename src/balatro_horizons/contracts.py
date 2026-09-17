"""Strict public contracts. Private engine objects never inherit these types."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Objective(StrictModel):
    kind: Literal["native_run_win"] = "native_run_win"
    target_ante: int = 8


class PublicCard(StrictModel):
    id: str
    label: str
    face_down: bool = False
    rank: str | None = None
    suit: str | None = None
    effects: list[str] = Field(default_factory=list)
    counters: dict[str, str] = Field(default_factory=dict)
    sell_price: str | None = None
    sellable: bool | None = False
    usable: bool = False
    min_targets: int = 0
    max_targets: int = 0


class PublicEffect(StrictModel):
    label: str
    effects: list[str] = Field(default_factory=list)


class SkipReward(PublicEffect):
    acquisition_condition: Literal["skip_this_blind"] = "skip_this_blind"


class PublicBlind(StrictModel):
    id: str
    label: str
    kind: str
    target: str
    skip_allowed: bool = False
    effects: list[str] = Field(default_factory=list)
    status: Literal["SELECT", "CURRENT", "UPCOMING", "DEFEATED", "SKIPPED", "UNKNOWN"] = "UNKNOWN"
    skip_reward: SkipReward | None = None
    disabled: bool | None = None


class PublicOffer(StrictModel):
    id: str
    label: str
    kind: str
    price: str | None
    acquire_allowed: bool = True
    buy_and_use_allowed: bool = False
    min_targets: int = 0
    max_targets: int = 0
    effects: list[str] = Field(default_factory=list)
    face_down: bool = False
    rank: str | None = None
    suit: str | None = None


class Progress(StrictModel):
    ante: int | None
    blind: str | None
    native_status: str | None = None
    round_number: int | None = None


class Resources(StrictModel):
    money: str | None
    hands: int | None
    discards: int | None
    chips: str | None
    target: str | None
    joker_capacity: int | None
    consumable_capacity: int | None
    credit_limit: str = "0"
    shop_reroll_cost: str | None = None
    boss_reroll_cost: str | None = None
    pack_choices_remaining: int | None = None


class DeckKnowledge(StrictModel):
    initial_count: int | None = None
    observed_draws: int | None = None
    composition: dict[str, int] = Field(default_factory=dict)
    remaining_exact: int | None = None
    provenance: Literal["public_history", "native_deck_view", "unknown"] = "unknown"


class SettlementRow(StrictModel):
    kind: Literal["blind", "hands", "discards", "interest", "other"]
    label: str
    dollars: str
    count: str | None = None


class Settlement(StrictModel):
    source: Literal["native_cashout_rows"]
    rows: list[SettlementRow]
    total: str
    omitted_rows: int = Field(default=0, ge=0)


class PublicState(StrictModel):
    progress: Progress
    resources: Resources
    hand: list[PublicCard]
    jokers: list[PublicCard]
    consumables: list[PublicCard]
    revealed_blinds: list[PublicBlind]
    offers: list[PublicOffer]
    public_deck_knowledge: DeckKnowledge
    hand_levels: dict[str, str] = Field(default_factory=dict)
    persistent_effects: list[str] = Field(default_factory=list)
    owned_vouchers: list[PublicEffect] | None = None
    pending_tags: list[PublicEffect] | None = None
    settlement: Settlement | None = None


class PublicObjectReference(StrictModel):
    area: str
    id: str
    label: str


class PublicFieldChange(StrictModel):
    path: list[str]
    before: JsonValue
    after: JsonValue


class PublicTransaction(StrictModel):
    version: Literal["public_transaction_v1"] = "public_transaction_v1"
    quote_source: Literal["pre_action_public_observation"] = "pre_action_public_observation"
    actual_charge_source: Literal["not_observed"] = "not_observed"
    quoted_cash_charge: str | None = None
    quoted_cash_proceeds: str | None = None
    actual_cash_charge: str | None = None
    actual_cash_proceeds: str | None = None
    cash_before: str | None
    cash_after: str | None
    net_cash_change: str | None


class LastAction(StrictModel):
    version: Literal["public_delta_v1"] = "public_delta_v1"
    source: Literal["observed_public_states"] = "observed_public_states"
    from_observation_id: int
    to_observation_id: int
    action_type: str
    selected_objects: list[PublicObjectReference]
    added_objects: list[PublicObjectReference]
    removed_objects: list[PublicObjectReference]
    changes: list[PublicFieldChange]
    transaction: PublicTransaction | None = None


class RecentPublicEvent(StrictModel):
    event_id: str
    event_type: str
    summary: str


class RemainingBudget(StrictModel):
    game_actions: int
    provider_calls: int
    helper_calls_this_decision: int = 0


class Observation(StrictModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    episode_id: str
    observation_id: int
    public_state_hash: str
    objective: Objective
    phase: str
    state: PublicState
    available_action_types: list[str]
    action_constraints: dict[str, dict[str, int | str | bool | list[str]]]
    recent_public_events: list[RecentPublicEvent]
    memory: str
    remaining_budget: RemainingBudget
    last_action: LastAction | None = None


class SelectBlind(StrictModel):
    type: Literal["select_blind"]
    blind_id: str


class SkipBlind(StrictModel):
    type: Literal["skip_blind"]
    blind_id: str


class PlayHand(StrictModel):
    type: Literal["play_hand"]
    card_ids: list[str]


class Discard(StrictModel):
    type: Literal["discard"]
    card_ids: list[str]


class Reorder(StrictModel):
    type: Literal["reorder"]
    area: Literal["hand", "jokers", "consumables"]
    ordered_ids: list[str]


class Buy(StrictModel):
    type: Literal["buy"]
    offer_id: str
    mode: Literal["acquire", "buy_and_use"] = "acquire"
    target_ids: list[str] = Field(default_factory=list)


class Sell(StrictModel):
    type: Literal["sell"]
    owned_id: str


class UseConsumable(StrictModel):
    type: Literal["use_consumable"]
    consumable_id: str
    target_ids: list[str] = Field(default_factory=list)


class RerollShop(StrictModel):
    type: Literal["reroll_shop"]


class RerollBoss(StrictModel):
    type: Literal["reroll_boss"]


class ChoosePack(StrictModel):
    type: Literal["choose_pack"]
    offer_id: str
    target_ids: list[str] = Field(default_factory=list)


class SkipPack(StrictModel):
    type: Literal["skip_pack"]


class LeaveShop(StrictModel):
    type: Literal["leave_shop"]


class CashOut(StrictModel):
    type: Literal["cash_out"]


Action = (
    SelectBlind
    | SkipBlind
    | PlayHand
    | Discard
    | Reorder
    | Buy
    | Sell
    | UseConsumable
    | RerollShop
    | RerollBoss
    | ChoosePack
    | SkipPack
    | LeaveShop
    | CashOut
)


class ActionEnvelope(StrictModel):
    observation_id: int
    action: Annotated[Action, Field(discriminator="type")]
    memory_update: str | None = None
    decision_note: str | None = None


class Outcome(StrEnum):
    WIN = "WIN"
    GAME_LOSS = "GAME_LOSS"
    AGENT_PROTOCOL_FAILURE = "AGENT_PROTOCOL_FAILURE"
    AGENT_ABORT = "AGENT_ABORT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CAMPAIGN_INTERRUPTED = "CAMPAIGN_INTERRUPTED"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    OPERATOR_ABORT = "OPERATOR_ABORT"
    INVALID_EVALUATION = "INVALID_EVALUATION"


class EpisodeSummary(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    episode_id: str
    evidence_kind: Literal["SYNTHETIC_TEST", "NATIVE"]
    outcome: Outcome
    reason: str
    attempted_actions: int
    committed_actions: int
    provider_calls: int
    last_verified_observation_id: int | None
    terminal_event_id: str
    journal_head: str


Judgment = Literal["acceptable", "concern", "likely_error", "unclear"]
Confidence = Literal["low", "medium", "high"]


class AnnotationInput(StrictModel):
    start_decision: int
    end_decision: int
    judgment: Judgment
    categories: list[str] = Field(default_factory=list)
    horizons_in_tension: list[Literal["immediate", "near_term", "long_term"]] = Field(
        default_factory=list
    )
    mechanism_summary: str
    alternative_actions: list[str] = Field(default_factory=list)
    confidence: Confidence
    evidence_event_ids: list[str] = Field(default_factory=list)
    intervention_refs: list[str] = Field(default_factory=list)
    annotation_id: str | None = None


class Exposure(StrictModel):
    max_event_seen: int
    outcome_seen: bool = False
    model_identity_seen: bool = False
    prior_seed_exposure: bool = False


class AnnotationRecord(AnnotationInput):
    annotation_id: str
    episode_id: str
    reviewer_id: str
    revision: int
    created_at: str
    review_mode: Literal["prospective", "retrospective", "mixed"]
    exposure: Exposure
