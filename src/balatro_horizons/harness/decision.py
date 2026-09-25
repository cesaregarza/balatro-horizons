"""Decision dispatch, helper feedback, and action validation for the harness."""

from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.harness.context.build import HELPER_EXHAUSTED_MESSAGE, decision_context
from balatro_horizons.harness.contract import Operation, Policy, ProviderPolicy
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.provider import OperatorAbort, ProviderRuntimeMixin
from balatro_horizons.harness.skills import read_guide
from balatro_horizons.harness.tool_interface import ACTION_MODELS
from balatro_horizons.harness.transport import ProtocolFailure

_PROTOCOL_MESSAGES = {
    "NO_OPERATION": "No operation was returned. Call exactly one available named tool.",
    "MULTIPLE_OPERATIONS": "Multiple operations were returned. Call exactly one available named tool.",
    "UNAVAILABLE_TOOL": (
        "That tool is unavailable in the current phase. Choose from the available "
        "gameplay tools or a helper tool."
    ),
    "INVALID_OPERATION_JSON": (
        "Tool arguments were not valid JSON. Call one named tool with a JSON object "
        "matching its schema."
    ),
    "TOOL_ARGUMENTS_MUST_BE_OBJECT": (
        "Tool arguments must be one JSON object matching the selected tool schema."
    ),
    "PROVIDER_RESPONSE_INCOMPLETE": (
        "The provider response ended before one complete operation. Return one concise tool call."
    ),
    "PROVIDER_CONTEXT_LIMIT": (
        "The provider reached its context limit before completing an operation."
    ),
    "PROVIDER_REFUSAL": "The provider declined to return an operation.",
    "LEGACY_MEMORY_UPDATE_NOT_ALLOWED": (
        "Use set_run_note or delete_run_note; gameplay actions do not replace your notebook."
    ),
    "UNAVAILABLE_TOOL_ARGUMENT": "This harness version does not support that tool argument.",
    "INVALID_MODEL_REFERENCE": "Use the integer ID shown in the current public view.",
    "UNKNOWN_MODEL_REFERENCE": "That integer ID is unknown. Use a visible current object ID.",
    "RUN_NOTEBOOK_LIMIT": (
        "The attached note exceeds notebook capacity. Shorten it or keep note_update null; "
        "neither the edit nor the action executed."
    ),
    "INVALID_RUN_NOTE_KEY": (
        "Use a nonblank note key of 1-64 characters without control characters; "
        "neither the edit nor the action executed."
    ),
    "RUN_NOTE_NOT_FOUND": (
        "That note key does not exist. Correct it or keep note_update null; "
        "neither the edit nor the action executed."
    ),
}


class DecisionRuntimeMixin(ProviderRuntimeMixin):
    """Implement one or more provider/helper turns for a public observation."""

    def _decision(self, observation):
        self._check_protocol_integrity()
        exchanges = []
        helper_count = invalid = 0
        while True:
            self._check_decision_stop()
            ctx, delivered = self._decision_context(observation, exchanges, helper_count)
            raw = None
            try:
                raw = self._dispatch_operation(ctx, delivered, observation)
                operation = Operation.validate_python(raw)
                if operation.kind == "abort":
                    return None, "AGENT_ABORT"
                if operation.kind != "action":
                    result = self._handle_helper_operation(
                        operation, raw, observation, helper_count
                    )
                    helper_count += 1
                    exchanges.append(self._exchange(raw, result))
                    continue
                return self._validated_action(operation, observation), None
            except (ValidationError, InvalidAction, ProtocolFailure) as error:
                invalid += 1
                error = self._normalize_invalid(error, ctx, helper_count)
                code = self._invalid_code(error)
                feedback = self._tool_feedback(error, code, observation)
                if code == "HELPER_LIMIT_REACHED":
                    feedback.update(helper_calls_remaining=0, message=HELPER_EXHAUSTED_MESSAGE)
                self._log_rejected(code, invalid, feedback, observation)
                if invalid >= self.limits.max_consecutive_invalid_actions:
                    return None, "AGENT_PROTOCOL_FAILURE"
                exchanges.append(self._exchange(raw, feedback))

    def _check_protocol_integrity(self):
        if self.protocol and getattr(self, "execution_implementation_hash",
                                     self.protocol["implementation_hash"]) != implementation_fingerprint():
            raise HarnessFailure("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED", stage="decision_start")

    def _check_decision_stop(self):
        if self.stop.is_set():
            raise OperatorAbort

    def _decision_context(self, observation, exchanges, helper_count):
        ctx, delivered = decision_context(
            observation,
            exchanges,
            byte_limit=self.limits.max_request_bytes,
            skills=self.rules.get("skills", []),
            frozen=self.protocol,
            notebook=self.notebook.view(),
            helper_remaining=max(
                0, self.limits.max_helper_calls_per_decision - helper_count
            ),
            working_memory=self.working_memory.view(),
            references=self.model_references,
        )
        ctx.observation["remaining_budget"]["provider_calls"] = (
            self.limits.max_provider_calls - self.calls
        )
        ctx.observation["remaining_budget"]["helper_calls_this_decision"] = helper_count
        ctx.observation["remaining_budget"]["helper_calls_remaining"] = max(
            0, self.limits.max_helper_calls_per_decision - helper_count
        )
        self.log(
            "agent_context",
            {"context": dict(ctx), "exchanges": delivered},
            actor="agent",
            observation_id=observation.observation_id,
        )
        return ctx, delivered

    def _dispatch_operation(self, ctx, delivered, observation):
        raw = None
        self.active_policy = self._policy_for_decision()
        self.action_actor = self.policy.actor if isinstance(self.policy, Policy) else "agent"
        raw = (
            self._provider(self.active_policy, ctx, delivered)
            if isinstance(self.active_policy, ProviderPolicy)
            else self.active_policy.decide(ctx, delivered)
        )
        self.log(
            "agent_operation",
            {"operation": raw},
            actor=self.action_actor,
            observation_id=observation.observation_id,
        )
        return raw

    def _handle_helper_operation(self, operation, raw, observation, helper_count):
        if helper_count >= self.limits.max_helper_calls_per_decision:
            raise ProtocolFailure("HELPER_LIMIT_REACHED")
        if operation.kind in ("set_run_note", "delete_run_note"):
            mutation, result = self.notebook.propose(
                operation.kind, operation.key, getattr(operation, "text", None)
            )
            if mutation is not None:
                self.log(
                    "run_note", mutation, actor="agent",
                    observation_id=observation.observation_id,
                )
                self.notebook.apply(mutation)
            result["game_advanced"] = False
        else:
            try:
                result = helper(
                    operation,
                    self.history_prefix + self.store.events(self.eid),
                    self.rules,
                    observation=observation,
                    references=(self.model_references
                                if isinstance(self.active_policy, ProviderPolicy) else None),
                )
            except (ValueError, ArithmeticError, SyntaxError):
                result = {"error": "INVALID_HELPER_REQUEST"}
        if result.get("reference") == "balatro_guide":
            result = self._fit_guide_result(result)
        self.log(
            "helper_result",
            {"operation": raw, "result": result},
            observation_id=observation.observation_id,
        )
        return result

    def _validated_action(self, operation, observation):
        self.attempted += 1
        if operation.envelope.memory_update is not None:
            raise ProtocolFailure("LEGACY_MEMORY_UPDATE_NOT_ALLOWED")
        validate_action(
            operation.envelope,
            observation,
            memory_limit=self.limits.memory_max_characters,
        )
        if operation.note_update is not None:
            self._apply_note(operation.note_update, observation)
        return operation.envelope

    def _apply_note(self, edit, observation):
        kind = "delete_run_note" if edit.text is None else "set_run_note"
        mutation, result = self.notebook.propose(kind, edit.key, edit.text)
        if mutation is None:
            raise ProtocolFailure(
                result["error"], **{key: value for key, value in result.items() if key != "error"}
            )
        # Validate BOTH first, then journal the note before native execution.
        # A failed native action must never roll back an accepted durable edit.
        self.log("run_note", mutation, actor="agent", observation_id=observation.observation_id)
        self.notebook.apply(mutation)

    def _normalize_invalid(self, error, ctx, helper_count):
        if (
            isinstance(error, ProtocolFailure)
            and error.code == "UNAVAILABLE_TOOL"
            and helper_count >= self.limits.max_helper_calls_per_decision
            and error.details.get("attempted_tool") in {
                tool["name"] for tool in ctx.tools
                if tool["name"] not in ACTION_MODELS and tool["name"] != "abort_run"
            }
        ):
            return ProtocolFailure("HELPER_LIMIT_REACHED", attempted_tool=error.details["attempted_tool"])
        return error

    @staticmethod
    def _invalid_code(error):
        if isinstance(error, (InvalidAction, ProtocolFailure)):
            return error.code
        return "INVALID_OPERATION_SCHEMA"

    def _log_rejected(self, code, invalid, feedback, observation):
        self.log(
            "action_rejected",
            {"code": code, "consecutive_invalid": invalid, "feedback": feedback},
            actor=self.action_actor,
            observation_id=observation.observation_id,
        )

    def _fit_guide_result(self, result):
        from balatro_horizons.harness.context.present import PAGE_BYTES

        # Keep each returned guide page within the byte bound used by the context builder.
        key = result["key"] + "#offset=" + str(result["offset"])
        return read_guide(self.rules, key, PAGE_BYTES)

    def _exchange(self, raw, result):
        exchange = {"operation": raw if raw is not None else {"kind": "invalid"}, "result": result}
        provider = self.active_policy if isinstance(self.active_policy, ProviderPolicy) else None
        exchange["tool_call"] = provider.last_tool_call if provider is not None else None
        turn = provider.last_provider_turn if provider is not None else None
        if turn is not None:
            exchange["provider_turn"] = turn
        return exchange

    def _tool_feedback(self, error, code, observation):
        feedback = {
            "error": code,
            "game_advanced": False,
            "observation_id": observation.observation_id,
            "available_gameplay_tools": observation.available_action_types,
        }
        if isinstance(error, ValidationError):
            feedback["fields"] = [
                {"path": list(item["loc"]), "problem": item["type"]}
                for item in error.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
        self._add_feedback_details(feedback, error, code, observation)
        return feedback

    @staticmethod
    def _add_feedback_details(feedback, error, code, observation):
        if code == "UNKNOWN_BLIND" and observation.state.revealed_blinds:
            feedback["current_blind_id"] = observation.state.revealed_blinds[0].id
            feedback["message"] = (
                "Only the current blind can be fought or skipped. Select does not award its skip tag."
            )
        elif code in ("INVALID_CARD_SELECTION", "INVALID_CARD_COUNT", "FORCED_CARD_REQUIRED"):
            feedback["current_hand_ids"] = [card.id for card in observation.state.hand]
            feedback["constraints"] = observation.action_constraints
        elif code == "INVALID_OPERATION_SCHEMA":
            feedback["message"] = (
                "Use one named tool with its flat arguments; do not nest an action envelope. "
                "Notes belong beside the action parameters."
            )
        elif isinstance(error, ProtocolFailure):
            feedback.update(error.details)
            feedback["message"] = _PROTOCOL_MESSAGES.get(
                code,
                "The provider response did not contain one valid operation; "
                "call one available named tool.",
            )
