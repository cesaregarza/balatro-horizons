"""The game boundary consumed by the runner and native evaluator."""

from typing import Any, Protocol, runtime_checkable

from balatro_horizons.contracts import Action
from balatro_horizons.observations.projection import HandleIssuer


class NativeFailure(RuntimeError):
    """Native execution was not established; it must not be scored as agent error."""

    def __init__(self, code: str, *, name: str | None = None):
        super().__init__(code)
        self.code = code
        self.name = name


class NativeRejected(ValueError):
    """The native engine established that a public action was illegal."""

    def __init__(self, code: str, *, name: str | None = None):
        super().__init__(code)
        self.code = code
        self.name = name


# This is the classification contract shared with the Lua dispatcher. Unknown
# bridge errors remain infrastructure failures, never guessed legality errors.
NOT_ALLOWED_CODES = frozenset({
    "UNKNOWN_CARD", "INVALID_TARGET_COUNT", "INVALID_TARGETS", "SELL_NOT_AVAILABLE",
    "UNAFFORDABLE", "CAPACITY", "USE_NOT_AVAILABLE", "INVALID_CARD_COUNT",
    "INVALID_SELECTION", "FORCED_CARD_REQUIRED", "PACK_NOT_OPEN",
    "REROLL_NOT_AVAILABLE", "UNKNOWN_ACTION",
})
INFRASTRUCTURE_CODES = frozenset({
    "NOT_READY", "BUSY", "ACTION_STATUS_UNKNOWN", "REQUEST_ID_REUSED",
})
HARNESS_FAULT_CODES = frozenset({
    "UNAUTHORIZED", "METHOD_FORBIDDEN", "PATH_FORBIDDEN",
})
ERROR_NAMES = {
    **dict.fromkeys(NOT_ALLOWED_CODES, "NOT_ALLOWED"),
    **dict.fromkeys(INFRASTRUCTURE_CODES, "INFRASTRUCTURE"),
    **dict.fromkeys(HARNESS_FAULT_CODES, "HARNESS_FAULT"),
}

RPC_METHODS = frozenset({
    "health", "bh_inspect", "bh_action", "bh_request_status", "bh_rules",
    "bh_fixture", "start", "menu", "save", "load", "select", "skip",
    "cash_out", "next_round", "reroll", "rearrange", "pack",
})


@runtime_checkable
class GameSession(Protocol):
    """Runner-facing game operations; no method is a provider tool by itself."""

    evidence_kind: str

    def wait_ready(self) -> None: ...
    def observe_private(self) -> dict[str, Any]: ...
    def terminal_status(self) -> str | None: ...
    def apply_public_action(
        self, action: Action, issuer: HandleIssuer, request_id: str | None = None
    ) -> None: ...
    def checkpoint(self) -> dict[str, Any]: ...
    def restore(self, snapshot: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class EvaluatorSession(GameSession, Protocol):
    """Evaluator-only operations cannot be supplied as provider tools."""

    def start_run(self, deck: str, stake: str, seed: str) -> None: ...
    def fixture(self, case: str) -> dict[str, Any]: ...
    def rules(self) -> dict[str, Any]: ...
    def inspect_raw(self) -> dict[str, Any]: ...
