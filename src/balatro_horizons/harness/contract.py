"""The policy boundary and the single surviving operation schema."""

from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import Field, TypeAdapter

from balatro_horizons.agents.tool_interface import INSPECT_SECTIONS
from balatro_horizons.config import (
    DEFAULT_HISTORY_PAGE_EVENTS,
    MAX_ABORT_REASON_CHARACTERS,
    MAX_ARITHMETIC_CHARACTERS,
    MAX_HISTORY_PAGE_EVENTS,
    ModelConfig,
)
from balatro_horizons.contracts import ActionEnvelope, StrictModel
from balatro_horizons.game.contract import GameSession


class NoteUpdate(StrictModel):
    key: str
    text: str | None


class Submit(StrictModel):
    kind: Literal["action"]
    envelope: ActionEnvelope


class WorkingSubmit(Submit):
    note_update: NoteUpdate | None = None


class Rules(StrictModel):
    kind: Literal["rules"]
    key: str


class Skill(StrictModel):
    kind: Literal["skill"]
    name: str = Field(pattern=r"^balatro-[a-z0-9-]+$", max_length=64)


class History(StrictModel):
    kind: Literal["history"]
    offset: int = Field(ge=0)
    limit: int = Field(default=DEFAULT_HISTORY_PAGE_EVENTS, ge=1, le=MAX_HISTORY_PAGE_EVENTS)


class Arithmetic(StrictModel):
    kind: Literal["arithmetic"]
    expression: str = Field(max_length=MAX_ARITHMETIC_CHARACTERS)


class Abort(StrictModel):
    kind: Literal["abort"]
    reason: str = Field(max_length=MAX_ABORT_REASON_CHARACTERS)


class InspectPage(StrictModel):
    kind: Literal["inspect_page"]
    section: Literal[*INSPECT_SECTIONS]
    offset: int = Field(default=0, ge=0)


class HistoryDetail(StrictModel):
    kind: Literal["history_detail"]
    offset: int = Field(ge=0)
    byte_offset: int = Field(default=0, ge=0)


class SetRunNote(StrictModel):
    kind: Literal["set_run_note"]
    key: str
    text: str


class DeleteRunNote(StrictModel):
    kind: Literal["delete_run_note"]
    key: str


class ActionResult(StrictModel):
    kind: Literal["action_result"]
    decision_id: int | None = Field(default=None, ge=0)
    episode_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    section: Literal["receipt", "before", "after"] = "receipt"
    byte_offset: int = Field(default=0, ge=0)


OperationValue = (
    WorkingSubmit | Rules | History | Arithmetic | Abort | Skill | InspectPage
    | HistoryDetail | SetRunNote | DeleteRunNote | ActionResult
)
Operation = TypeAdapter(Annotated[OperationValue, Field(discriminator="kind")])

# The context remains a dictionary until #12. Its current public keys are:
# prompt, rules_kernel, interface_version, tools, current_costs, observation,
# omitted_event_ids, run_notebook, working_memory, previous_action_outcome,
# allowed_tools, helper_status, notebook_maintenance, context_delivery,
# context_bytes_upper_bound, and skill_catalog_delivery.
Context = dict[str, Any]
Exchanges = list[dict[str, Any]]
RawOperation = dict[str, Any]


@runtime_checkable
class EnvironmentLockedGame(GameSession, Protocol):
    """A game carrying a verified lock; the byte-frozen fake has no such lock."""

    lock: dict[str, Any]


@runtime_checkable
class Policy(Protocol):
    """One decision interface shared by model, human, replay, and baselines."""

    interface: str
    name: str
    actor: str
    model: ModelConfig | None

    def decide(self, context: Context, exchanges: Exchanges) -> RawOperation: ...
    def on_decision_end(self) -> None: ...
    def on_commit(self) -> None: ...


@runtime_checkable
class ProviderPolicy(Policy, Protocol):
    """A policy whose transport is metered by the runner before it is sent."""

    model: ModelConfig
    last_tool_call: dict[str, Any] | None
    last_provider_turn: dict[str, Any] | None

    def request(self, context: Context, exchanges: Exchanges) -> dict[str, Any]:
        """Build a request body from public context and this decision's exchanges."""
        ...

    def check_input(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Measure the request without contacting a provider."""
        ...

    def send(self, body: dict[str, Any]) -> dict[str, Any]:
        """Contact the configured provider and return its private response."""
        ...

    def usage_cost(self, response: dict[str, Any], reserved: float) -> float:
        """Return settled USD cost without mutating the spending ledger."""
        ...

    def parse(self, response: dict[str, Any]) -> RawOperation:
        """Decode one operation and retain provider continuation state."""
        ...


@runtime_checkable
class RoutedPolicy(Protocol):
    """Intervention wrappers select their next actor before each operation."""

    @property
    def active_policy(self) -> Policy: ...


@runtime_checkable
class NamedPolicy(Protocol):
    """Frozen test policies may only provide a name, not the full contract."""

    name: str
