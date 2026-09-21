"""One in-flight native action, explicit agent operations, immutable evidence."""

import json
import threading
import traceback
import uuid
from copy import deepcopy

from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.budget import (
    BudgetExhausted,
    Spending,
    reservation_usd,
    validate_paid_configuration,
)
from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.agents.frozen import freeze_protocol, restore_protocol
from balatro_horizons.agents.notebook import RunNotebook, restore_notebook
from balatro_horizons.agents.protocol import KERNEL, decision_context, helper
from balatro_horizons.agents.providers import ProtocolFailure, ProviderFailure
from balatro_horizons.agents.skills import prepare_rules, read_guide, restore_knowledge
from balatro_horizons.agents.tool_interface import ACTION_MODELS
from balatro_horizons.agents.working_memory import WorkingMemory, restore_working_memory
from balatro_horizons.config import RECENT_PUBLIC_EVENT_LIMIT
from balatro_horizons.contracts import Observation, RecentPublicEvent, RemainingBudget
from balatro_horizons.evidence.provenance import (
    continuation_fingerprint,
    implementation_fingerprint,
)
from balatro_horizons.game.contract import (
    ERROR_NAMES,
    PUBLIC_NATIVE_FALLBACKS,
    GameSession,
    NativeFailure,
    NativeRejected,
)
from balatro_horizons.harness.contract import (
    EnvironmentLockedGame,
    NamedPolicy,
    Operation,
    Policy,
    ProviderPolicy,
    RoutedPolicy,
)
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.observations.projection import HandleIssuer, project_public
from balatro_horizons.storage.journal import digest


class OperatorAbort(RuntimeError):
    pass


class Runner:
    def __init__(self, store, config, game: GameSession, policy: Policy, *, stop=None, spending=None, rules=None,
                 prompt_bytes=None):
        self.store, self.config, self.game, self.policy = (
            store,
            config.model_copy(deep=True),
            game,
            policy,
        )
        self.stop = stop or threading.Event()
        self.limits = self.config.budgets
        self.spending = spending
        self.rules = rules or {"core": KERNEL}
        self.committed = self.calls = self.attempted = 0
        self.cost = 0.0
        self.memory = ""
        self.notebook = RunNotebook(self.limits.memory_max_characters)
        self.working_memory = WorkingMemory()
        self.recent = []
        self.issuer = HandleIssuer()
        self.eid = None
        self.observation = None
        self.protocol = None
        self.prompt_bytes = prompt_bytes
        self.active_policy = policy

    def _policy_for_decision(self):
        # An intervention can become a metered provider after its last override.
        policy = self.policy
        while isinstance(policy, RoutedPolicy):
            active = policy.active_policy
            if active is policy:
                break
            policy = active
        return policy

    def log(self, kind, payload, **kwargs):
        event = self.store.append(self.eid, kind, payload, **kwargs)
        self.working_memory.consume(event)
        return event

    def _record_failure(self, error):
        # Stack locations help diagnose failures without serializing secrets from
        # locals, exception messages, provider bodies, or source-code lines.
        frames = traceback.extract_tb(error.__traceback__)
        try:
            self.store.private_json(self.eid, "failure-diagnostic.json", {
                "exception_type": type(error).__name__,
                "diagnostic": error.public() if isinstance(error, HarnessFailure) else None,
                "frames": [{"file": f.filename, "line": f.lineno, "function": f.name}
                           for f in frames],
            })
        except OSError:
            pass  # A diagnostic-write failure must not prevent the terminal record.

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

    def _provider(self, policy: ProviderPolicy, ctx, exchanges):
        body = policy.request(ctx, exchanges)
        if self.stop.is_set():
            raise OperatorAbort
        if self.calls >= self.limits.max_provider_calls:
            raise BudgetExhausted("PROVIDER_CALL_LIMIT")
        input_measurement = policy.check_input(body)
        if input_measurement is not None:
            self.log("provider_input_check", input_measurement,
                     observation_id=self.observation.observation_id)
        model = policy.model
        reserve = reservation_usd(model, self.limits)
        for attempt in range(self.limits.max_transport_attempts):
            if self.stop.is_set():
                raise OperatorAbort
            if self.calls >= self.limits.max_provider_calls:
                raise BudgetExhausted("PROVIDER_CALL_LIMIT")
            request_id = uuid.uuid4().hex
            self.spending.reserve(
                request_id,
                self.eid,
                reserve,
                self.limits.max_episode_cost_usd,
                prior_cost=self.prior_cost,
            )
            self.calls += 1
            self.cost += reserve
            self.log(
                "provider_request",
                {"body": body, "reserved_usd": reserve, "attempt": attempt + 1},
                actor="agent",
                request_id=request_id,
                observation_id=self.observation.observation_id,
            )
            try:
                response = policy.send(body)
            except ProviderFailure as error:
                self.log(
                    "provider_error",
                    {"code": error.code, "provider_code": error.provider_code, "usage": "unknown"},
                    request_id=request_id,
                )
                if not error.retryable or attempt + 1 == self.limits.max_transport_attempts:
                    raise
                if self.stop.wait(min(2**attempt, 4)):
                    raise OperatorAbort from None
                continue
            actual = policy.usage_cost(response, reserve)
            self.spending.settle(request_id, actual)
            self.cost += actual - reserve
            self.log(
                "provider_response",
                {"body": response, "cost_usd": actual},
                actor="agent",
                request_id=request_id,
            )
            return policy.parse(response)

    def _decision(self, observation):
        if self.protocol and self.protocol["implementation_hash"] != implementation_fingerprint():
            raise HarnessFailure("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED", stage="decision_start")
        exchanges = []
        helper_count = invalid = 0
        while True:
            if self.stop.is_set():
                raise OperatorAbort
            ctx, delivered_exchanges = decision_context(
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
            )
            ctx["observation"]["remaining_budget"]["provider_calls"] = (
                self.limits.max_provider_calls - self.calls
            )
            ctx["observation"]["remaining_budget"]["helper_calls_this_decision"] = helper_count
            ctx["observation"]["remaining_budget"]["helper_calls_remaining"] = max(
                0, self.limits.max_helper_calls_per_decision - helper_count
            )
            self.log(
                "agent_context",
                {"context": ctx, "exchanges": delivered_exchanges},
                actor="agent",
                observation_id=observation.observation_id,
            )
            try:
                raw = None
                self.active_policy = self._policy_for_decision()
                self.action_actor = (
                    self.policy.actor if isinstance(self.policy, Policy) else "agent"
                )
                raw = (
                    self._provider(self.active_policy, ctx, delivered_exchanges)
                    if isinstance(self.active_policy, ProviderPolicy)
                    else self.active_policy.decide(ctx, delivered_exchanges)
                )
                self.log(
                    "agent_operation",
                    {"operation": raw},
                    actor=self.action_actor,
                    observation_id=observation.observation_id,
                )
                operation = Operation.validate_python(raw)
                if operation.kind == "abort":
                    return None, "AGENT_ABORT"
                if operation.kind != "action":
                    if helper_count >= self.limits.max_helper_calls_per_decision:
                        raise ProtocolFailure("HELPER_LIMIT_REACHED")
                    helper_count += 1
                    if operation.kind in ("set_run_note", "delete_run_note"):
                        mutation, result = self.notebook.propose(
                            operation.kind, operation.key, getattr(operation, "text", None)
                        )
                        if mutation is not None:
                            self.log("run_note", mutation, actor="agent",
                                     observation_id=observation.observation_id)
                            self.notebook.apply(mutation)
                        result["game_advanced"] = False
                    else:
                        try:
                            result = helper(
                                operation,
                                self.history_prefix + self.store.events(self.eid),
                                self.rules,
                                observation=observation,
                            )
                        except (ValueError, ArithmeticError, SyntaxError):
                            result = {"error": "INVALID_HELPER_REQUEST"}
                    if result.get("reference") == "balatro_guide":
                        result = self._fit_guide_result(observation, exchanges, raw, result)
                    self.log(
                        "helper_result",
                        {"operation": raw, "result": result},
                        observation_id=observation.observation_id,
                    )
                    exchanges.append(self._exchange(raw, result))
                    continue
                self.attempted += 1
                if operation.envelope.memory_update is not None:
                    raise ProtocolFailure("LEGACY_MEMORY_UPDATE_NOT_ALLOWED")
                validate_action(
                    operation.envelope, observation, memory_limit=self.limits.memory_max_characters
                )
                if operation.note_update is not None:
                    edit = operation.note_update
                    kind = "delete_run_note" if edit.text is None else "set_run_note"
                    mutation, result = self.notebook.propose(kind, edit.key, edit.text)
                    if mutation is None:
                        raise ProtocolFailure(result["error"], **{
                            k: v for k, v in result.items() if k != "error"
                        })
                    # Validate BOTH first, then journal the note before native execution.
                    # A failed native action must never roll back an accepted durable edit.
                    self.log("run_note", mutation, actor="agent",
                             observation_id=observation.observation_id)
                    self.notebook.apply(mutation)
                return operation.envelope, None
            except (ValidationError, InvalidAction, ProtocolFailure) as error:
                invalid += 1
                if (
                    isinstance(error, ProtocolFailure)
                    and error.code == "UNAVAILABLE_TOOL"
                    and helper_count >= self.limits.max_helper_calls_per_decision
                    and error.details.get("attempted_tool") in {
                        tool["name"] for tool in ctx["tools"]
                        if tool["name"] not in ACTION_MODELS and tool["name"] != "abort_run"
                    }
                ):
                    error = ProtocolFailure(
                        "HELPER_LIMIT_REACHED",
                        attempted_tool=error.details["attempted_tool"],
                    )
                code = (
                    error.code
                    if isinstance(error, InvalidAction)
                    or isinstance(error, ProtocolFailure)
                    else "INVALID_OPERATION_SCHEMA"
                )
                feedback = self._tool_feedback(error, code, observation)
                if code == "HELPER_LIMIT_REACHED":
                    feedback.update(helper_calls_remaining=0,
                                    message="Helper allowance exhausted. Choose a permitted gameplay action or abort_run.")
                self.log(
                    "action_rejected",
                    {"code": code, "consecutive_invalid": invalid, "feedback": feedback},
                    actor=self.action_actor,
                    observation_id=observation.observation_id,
                )
                if invalid >= self.limits.max_consecutive_invalid_actions:
                    return None, "AGENT_PROTOCOL_FAILURE"
                exchanges.append(self._exchange(raw, feedback))

    def _fit_guide_result(self, observation, exchanges, raw, result):
        # A shared conservative byte bound keeps paging independent of provider transport.
        key = result["key"] + "#offset=" + str(result["offset"])
        from balatro_horizons.agents.focused import PAGE_BYTES

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
                {"path": list(e["loc"]), "problem": e["type"]}
                for e in error.errors(include_input=False, include_context=False, include_url=False)
            ]
        if code == "UNKNOWN_BLIND" and observation.state.revealed_blinds:
            feedback["current_blind_id"] = observation.state.revealed_blinds[0].id
            feedback["message"] = (
                "Only the current blind can be fought or skipped. Select does not award its skip tag."
            )
        elif code in ("INVALID_CARD_SELECTION", "INVALID_CARD_COUNT", "FORCED_CARD_REQUIRED"):
            feedback["current_hand_ids"] = [c.id for c in observation.state.hand]
            feedback["constraints"] = observation.action_constraints
        elif code == "INVALID_OPERATION_SCHEMA":
            feedback["message"] = (
                "Use one named tool with its flat arguments; do not nest an action envelope. Notes belong beside the action parameters."
            )
        elif isinstance(error, ProtocolFailure):
            feedback.update(error.details)
            messages = {
                "NO_OPERATION": "No operation was returned. Call exactly one available named tool.",
                "MULTIPLE_OPERATIONS": "Multiple operations were returned. Call exactly one named tool.",
                "UNAVAILABLE_TOOL": "That tool is unavailable in the current phase. Choose from the available gameplay tools or a helper tool.",
                "INVALID_OPERATION_JSON": "Tool arguments were not valid JSON. Call one named tool with a JSON object matching its schema.",
                "TOOL_ARGUMENTS_MUST_BE_OBJECT": "Tool arguments must be one JSON object matching the selected tool schema.",
                "PROVIDER_RESPONSE_INCOMPLETE": "The provider response ended before one complete operation. Return one concise tool call.",
                "PROVIDER_CONTEXT_LIMIT": "The provider reached its context limit before completing an operation.",
                "PROVIDER_REFUSAL": "The provider declined to return an operation.",
                "LEGACY_MEMORY_UPDATE_NOT_ALLOWED": "Use set_run_note or delete_run_note; gameplay actions do not replace your notebook.",
                "UNAVAILABLE_TOOL_ARGUMENT": "This harness version does not support that tool argument.",
                "RUN_NOTEBOOK_LIMIT": "The attached note exceeds notebook capacity. Shorten it or keep note_update null; neither the edit nor the action executed.",
                "INVALID_RUN_NOTE_KEY": "Use a nonblank note key of 1-64 characters without control characters; neither the edit nor the action executed.",
                "RUN_NOTE_NOT_FOUND": "That note key does not exist. Correct it or keep note_update null; neither the edit nor the action executed.",
            }
            feedback["message"] = messages.get(
                code,
                "The provider response did not contain one valid operation; call one available named tool.",
            )
        return feedback

    def run(self, *, eid=None, manifest=None, private=None, resume=None, history_prefix=None):
        if isinstance(self.policy, Policy) and self.policy.model is not None:
            validate_paid_configuration(self.policy.model, self.limits)
        self.rules = (
            restore_knowledge(self.store, resume)
            if resume
            else prepare_rules(self.rules, self.config.skills)
        )
        self.protocol = (
            restore_protocol(self.store, resume)
            if resume
            else freeze_protocol(self.config, self.policy, self.rules, prompt_bytes=self.prompt_bytes)
        )
        if resume:
            from balatro_horizons.agents.frozen import episode_limits

            if self.protocol["episode_limits"] != episode_limits(self.config):
                raise ValueError("AGENT_PROTOCOL_CONFIGURATION_CHANGED")
            model = self.policy.model if isinstance(self.policy, Policy) else None
            if self.policy.name != "human" and self.protocol["model"] != (
                model.model_dump() if model is not None else None
            ):
                raise ValueError("AGENT_PROTOCOL_MODEL_CHANGED")
        if self.protocol["knowledge_hash"] != digest(self.rules):
            raise ValueError("AGENT_PROTOCOL_KNOWLEDGE_CHANGED")
        self.eid = eid or self.store.create(
            manifest
            or {
                "evidence_kind": self.game.evidence_kind,
                "config": self.config.public(),
                "agent": self.policy.name if isinstance(self.policy, NamedPolicy) else "model",
                "evaluation_eligible": False,
            },
            private or {},
        )
        self.store.private_json(self.eid, "knowledge.json", self.rules)
        self.store.private_json(self.eid, "agent-protocol.json", self.protocol)
        self.protocol_reference = {"episode_id": self.eid, "hash": digest(self.protocol)}
        self.knowledge_reference = {"episode_id": self.eid, "hash": digest(self.rules)}
        self.history_prefix = history_prefix or []
        if resume:
            self.notebook = restore_notebook(
                resume.get("run_notebook"), self.history_prefix, self.limits.memory_max_characters
            )
            self.working_memory = restore_working_memory(
                resume.get("working_memory"), self.history_prefix, resume["observation"]
            )
        self.spending = self.spending or Spending(
            self.store.root / "private_runs" / self.eid / "spending.json",
            self.limits.max_batch_cost_usd,
        )
        cost_context = None
        previous_action = None
        start = 0
        self.prior_cost = 0.0
        if resume:
            self.game.restore(resume["game"])
            from balatro_horizons.game.replay import check_private

            check_private(self.game, resume.get("continuation_hash"))
            self.issuer = HandleIssuer.restore(resume["issuer"])
            self.prior_cost = resume.get("cost", 0.0)
            self.memory = resume["memory"]
            self.committed = resume["committed"]
            self.calls = resume["calls"]
            self.recent = [RecentPublicEvent.model_validate(e) for e in resume["recent"]]
            start = resume["observation"]["observation_id"]
        self.log(
            "episode_start",
            {
                "evidence_kind": self.game.evidence_kind,
                "rules_hash": digest(self.rules),
                "knowledge": self.rules.get("guide", {"preset": "none", "protocol": "rules_only"}),
                "implementation_hash": implementation_fingerprint(),
                "environment_hash": digest(
                    self.game.lock if isinstance(self.game, EnvironmentLockedGame)
                    else {"kind": "synthetic"}
                ),
                "agent_protocol": deepcopy(self.protocol_reference),
            },
        )
        outcome, reason = "INFRASTRUCTURE_FAILURE", "UNEXPECTED_RUNNER_FAILURE"
        try:
            decision = start
            while True:
                if self.stop.is_set():
                    raise OperatorAbort
                self.game.wait_ready()
                raw = self.game.observe_private()
                self.store.private_json(self.eid, f"raw-{decision}.json", raw)
                if resume and decision == start:
                    self.observation = Observation.model_validate(resume["observation"])
                    self.observation.episode_id = self.eid
                else:
                    self.observation = project_public(
                        raw,
                        episode_id=self.eid,
                        observation_id=decision,
                        issuer=self.issuer,
                        memory=self.memory,
                        recent_events=self.recent[-RECENT_PUBLIC_EVENT_LIMIT:],
                        remaining_budget=RemainingBudget(
                            game_actions=self.limits.max_game_actions - self.committed,
                            provider_calls=self.limits.max_provider_calls - self.calls,
                        ),
                    )
                if previous_action is not None:
                    previous, action = previous_action
                    self.observation.last_action = last_action(previous, self.observation, action)
                obs_event = self.log(
                    "observation", self.observation.model_dump(mode="json"), observation_id=decision
                )
                terminal = self.game.terminal_status()
                if terminal:
                    outcome, reason = terminal, "VERIFIED_ENGINE_TERMINAL"
                    break
                if self.committed >= self.limits.max_game_actions:
                    raise BudgetExhausted("GAME_ACTION_LIMIT")
                try:
                    checkpoint = {
                        "game": self.game.checkpoint(),
                        "knowledge": self.knowledge_reference,
                        "agent_protocol": deepcopy(self.protocol_reference),
                        "issuer": self.issuer.snapshot(),
                        "observation": self.observation.model_dump(mode="json"),
                        "memory": self.memory,
                        "committed": self.committed,
                        "calls": self.calls,
                        "cost": self.prior_cost + self.cost,
                        "implementation_hash": implementation_fingerprint(),
                        "continuation_hash": continuation_fingerprint(raw),
                        "recent": [e.model_dump() for e in self.recent],
                        "public_prefix_hash": obs_event["hash"],
                        "run_notebook": self.notebook.snapshot(),
                        "working_memory": self.working_memory.view(),
                    }
                    self.store.private_json(self.eid, f"checkpoint-{decision}.json", checkpoint)
                    self.log(
                        "checkpoint_result",
                        {"decision": decision, "saved": True, "certified": False},
                        observation_id=decision,
                    )
                except (NativeFailure, NativeRejected, OSError, ValueError) as error:
                    if isinstance(error, (NativeFailure, NativeRejected)):
                        self._record_native_error(error, "checkpoint")
                    self.log(
                        "checkpoint_result",
                        {"decision": decision, "saved": False, "certified": False},
                        observation_id=decision,
                    )
                envelope, failure = self._decision(self.observation)
                if failure:
                    outcome, reason = failure, failure
                    break
                request_id = uuid.uuid4().hex
                self.log(
                    "action_intent",
                    envelope.model_dump(mode="json"),
                    actor=self.action_actor,
                    observation_id=decision,
                    request_id=request_id,
                )
                try:
                    self.game.apply_public_action(envelope.action, self.issuer, request_id)
                except NativeRejected as error:
                    self._record_native_error(error, "action")
                    self.log(
                        "action_rejected",
                        {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"},
                        observation_id=decision,
                        request_id=request_id,
                    )
                    outcome, reason = "INVALID_EVALUATION", self._native_code(
                        error, "NATIVE_PUBLIC_LEGALITY_MISMATCH"
                    )
                    break
                self.committed += 1
                previous_action = (self.observation, envelope.action)
                commit = self.log(
                    "action_commit",
                    envelope.model_dump(mode="json"),
                    actor=self.action_actor,
                    observation_id=decision,
                    request_id=request_id,
                )
                if isinstance(self.active_policy, Policy):
                    self.active_policy.on_decision_end()
                if isinstance(self.policy, Policy):
                    self.policy.on_commit()
                self.recent.append(
                    RecentPublicEvent(
                        event_id=obs_event["event_id"],
                        event_type="observation",
                        summary=json.dumps(
                            {
                                "phase": self.observation.phase,
                                "progress": self.observation.state.progress.model_dump(),
                                "resources": self.observation.state.resources.model_dump(),
                            }
                        ),
                    )
                )
                self.recent.append(
                    RecentPublicEvent(
                        event_id=commit["event_id"],
                        event_type="action_commit",
                        summary=json.dumps(envelope.action.model_dump(mode="json")),
                    )
                )
                self.recent = self.recent[-RECENT_PUBLIC_EVENT_LIMIT:]
                decision += 1
        except OperatorAbort:
            outcome, reason = "OPERATOR_ABORT", "OPERATOR_REQUEST"
        except BudgetExhausted as error:
            outcome, reason = error.outcome, str(error)
            cost_context = error.cost_context
        except NativeFailure as error:
            self._record_native_error(error, "runtime")
            outcome, reason = "INFRASTRUCTURE_FAILURE", self._native_code(
                error, "NATIVE_BRIDGE_FAILURE"
            )
            self.log("action_status_unknown", {"code": reason})
        except ProviderFailure as error:
            outcome, reason = "INFRASTRUCTURE_FAILURE", str(error)
            self.log("action_status_unknown", {"code": reason})
        except HarnessFailure as error:
            outcome, reason = "INFRASTRUCTURE_FAILURE", error.code
            self.log("harness_failure", error.public())
            self._record_failure(error)
        except Exception as error:
            # Exception text may contain private native state; only the type crosses.
            outcome, reason = "INFRASTRUCTURE_FAILURE", type(error).__name__
            self._record_failure(error)
        finally:
            self.store.finish(
                self.eid,
                {
                    "episode_id": self.eid,
                    "evidence_kind": self.game.evidence_kind,
                    "outcome": outcome,
                    "reason": reason,
                    "attempted_actions": self.attempted,
                    "committed_actions": self.committed,
                    "provider_calls": self.calls,
                    "agent_protocol": deepcopy(self.protocol_reference),
                    "cost_usd": self.cost,
                    **({"cost_context": cost_context} if cost_context is not None else {}),
                    "last_verified_observation_id": self.observation.observation_id
                    if self.observation
                    else None,
                },
            )
            try:
                self.game.close()
            except (NativeFailure, OSError) as error:
                if isinstance(error, NativeFailure):
                    self._record_native_error(error, "cleanup")
                self.store.private_json(
                    self.eid, "cleanup-error.json", {"code": "NATIVE_CLEANUP_FAILED"}
                )
        return self.store.summary(self.eid)
