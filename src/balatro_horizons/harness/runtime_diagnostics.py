"""Private diagnostics and exit classification for the episode runtime."""

import traceback
import uuid

from balatro_horizons.game.contract import (
    ERROR_NAMES,
    PUBLIC_NATIVE_FALLBACKS,
    NativeFailure,
)
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.money import BudgetExhausted
from balatro_horizons.harness.provider import OperatorAbort
from balatro_horizons.harness.transport import ProviderFailure


class RuntimeDiagnosticsMixin:
    """Keep private engine details out of the public episode journal."""

    def _record_failure(self, error):
        # Keep failure diagnostics to the type, allowlisted details, and stack locations.
        frames = traceback.extract_tb(error.__traceback__)
        try:
            self.store.private_json(
                self.eid,
                "failure-diagnostic.json",
                {
                    "exception_type": type(error).__name__,
                    "diagnostic": error.public() if isinstance(error, HarnessFailure) else None,
                    "frames": [
                        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                        for frame in frames
                    ],
                },
            )
        except OSError:
            # A diagnostic write must not mask the original runtime failure.
            pass

    @staticmethod
    def _native_code(error, fallback):
        code = getattr(error, "code", None)
        return code if isinstance(code, str) and (
            code in ERROR_NAMES or code in PUBLIC_NATIVE_FALLBACKS
        ) else fallback

    def _record_native_error(self, error, phase):
        """Retain the Lua envelope privately without exposing its message in the journal."""
        name = getattr(error, "name", None)
        record = {
            "phase": phase,
            "code": error.code if isinstance(getattr(error, "code", None), str) else None,
            "name": name if isinstance(name, str) else None,
        }
        raw_message = getattr(error, "raw_message", None)
        if isinstance(raw_message, str):
            record["message"] = raw_message
        self.store.private_json(self.eid, f"engine-error-{uuid.uuid4().hex}.json", record)

    def classify_exit(self, error):
        """Map private runtime exceptions to the public terminal outcome."""
        if error is None:
            outcome, reason = self._play_result
            return outcome, reason, None
        if isinstance(error, OperatorAbort):
            return "OPERATOR_ABORT", "OPERATOR_REQUEST", None
        if isinstance(error, BudgetExhausted):
            return error.outcome, str(error), error.cost_context
        if isinstance(error, NativeFailure):
            self._record_native_error(error, "runtime")
            reason = self._native_code(error, "NATIVE_BRIDGE_FAILURE")
            self.log("action_status_unknown", {"code": reason})
            return "INFRASTRUCTURE_FAILURE", reason, None
        if isinstance(error, ProviderFailure):
            reason = str(error)
            self.log("action_status_unknown", {"code": reason})
            return "INFRASTRUCTURE_FAILURE", reason, None
        if isinstance(error, HarnessFailure):
            self.log("harness_failure", error.public())
            self._record_failure(error)
            return "INFRASTRUCTURE_FAILURE", error.code, None
        # Exception text may contain private native state; only the type crosses.
        self._record_failure(error)
        return "INFRASTRUCTURE_FAILURE", type(error).__name__, None
