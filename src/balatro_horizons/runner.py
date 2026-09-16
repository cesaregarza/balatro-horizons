"""One in-flight native action, explicit agent operations, immutable evidence."""

import json
import threading
import uuid

from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.budget import BudgetExhausted, Spending
from balatro_horizons.agents.protocol import KERNEL, Operation, decision_context, helper
from balatro_horizons.agents.providers import ProtocolFailure, ProviderFailure
from balatro_horizons.agents.skills import prepare_rules, read_guide, restore_knowledge
from balatro_horizons.contracts import Observation, RecentPublicEvent, RemainingBudget
from balatro_horizons.engine.native import NativeFailure, NativeRejected
from balatro_horizons.engine.provenance import continuation_fingerprint, implementation_fingerprint
from balatro_horizons.observations.projection import HandleIssuer, project_public
from balatro_horizons.storage.journal import digest


class OperatorAbort(RuntimeError):
    pass


class Runner:
    def __init__(self, store, config, game, policy, *, stop=None, spending=None, rules=None):
        self.store, self.config, self.game, self.policy = store, config, game, policy
        self.stop = stop or threading.Event()
        self.limits = config.budgets
        self.spending = spending
        self.rules = rules or {"core": KERNEL}
        self.committed = self.calls = self.attempted = 0
        self.cost = 0.0
        self.memory = ""
        self.recent = []
        self.issuer = HandleIssuer()
        self.eid = None
        self.observation = None

    def log(self, kind, payload, **kwargs):
        return self.store.append(self.eid, kind, payload, **kwargs)

    def _provider(self, ctx, exchanges):
        body = self.policy.request(ctx, exchanges)
        model = self.policy.model
        reserve = (
            self.limits.max_input_tokens_per_call * model.maximum_input_usd_per_million
            + self.limits.max_output_tokens_per_call * model.output_usd_per_million
        ) / 1_000_000
        for attempt in range(self.limits.max_transport_attempts):
            if self.stop.is_set():
                raise OperatorAbort
            if self.calls >= self.limits.max_provider_calls:
                raise BudgetExhausted("PROVIDER_CALL_LIMIT")
            request_id = uuid.uuid4().hex
            self.spending.reserve(
                request_id, self.eid, reserve, self.limits.max_episode_cost_usd - self.prior_cost
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
                response = self.policy.send(body)
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
            actual = self.policy.usage_cost(response, reserve)
            self.spending.settle(request_id, actual)
            self.cost += actual - reserve
            self.log(
                "provider_response",
                {"body": response, "cost_usd": actual},
                actor="agent",
                request_id=request_id,
            )
            return self.policy.parse(response)
        raise ProviderFailure("PROVIDER_RETRIES_EXHAUSTED")

    def _decision(self, observation):
        exchanges = []
        helper_count = invalid = 0
        while True:
            if self.stop.is_set():
                raise OperatorAbort
            ctx, delivered_exchanges = decision_context(
                observation,
                exchanges,
                byte_limit=self.limits.max_input_tokens_per_call,
                interface=getattr(self.policy, "interface", "operate_v1"),
                skills=self.rules.get("skills", []),
            )
            ctx["observation"]["remaining_budget"]["provider_calls"] = (
                self.limits.max_provider_calls - self.calls
            )
            ctx["observation"]["remaining_budget"]["helper_calls_this_decision"] = helper_count
            if getattr(self.policy, "interface", "operate_v1") in ("tools_v3", "tools_v4"):
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
                self.action_actor = getattr(self.policy, "actor", "agent")
                raw = (
                    self._provider(ctx, delivered_exchanges)
                    if self.policy.paid
                    else self.policy.decide(ctx, delivered_exchanges)
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
                        raise BudgetExhausted("HELPER_CALL_LIMIT")
                    helper_count += 1
                    try:
                        result = helper(
                            operation,
                            self.history_prefix + self.store.events(self.eid),
                            self.rules,
                            observation=observation,
                            interface=getattr(self.policy, "interface", "operate_v1"),
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
                validate_action(
                    operation.envelope, observation, memory_limit=self.limits.memory_max_characters
                )
                return operation.envelope, None
            except (ValidationError, InvalidAction, ProtocolFailure) as error:
                invalid += 1
                code = (
                    error.code if isinstance(error, InvalidAction) else "INVALID_OPERATION_SCHEMA"
                )
                feedback = {"error": code}
                if getattr(self.policy, "interface", "operate_v1") in (
                    "tools_v2",
                    "tools_v3",
                    "tools_v4",
                ):
                    feedback = self._tool_feedback(error, code, observation)
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
        if getattr(self.policy, "interface", "operate_v1") in ("tools_v3", "tools_v4"):
            from balatro_horizons.agents.focused import PAGE_BYTES

            return read_guide(self.rules, key, PAGE_BYTES)
        for page_bytes in (4096, 2048, 1024, 512, 256, 128):
            page = read_guide(self.rules, key, page_bytes)
            proposed = exchanges + [self._exchange(raw, page)]
            ctx, delivered = decision_context(
                observation,
                proposed,
                byte_limit=self.limits.max_input_tokens_per_call,
                interface=getattr(self.policy, "interface", "operate_v1"),
                skills=self.rules.get("skills", []),
            )
            encoded = json.dumps({"context": ctx, "exchanges": delivered}, ensure_ascii=False)
            if (
                len(json.dumps(encoded, ensure_ascii=False).encode()) + 4096
                <= self.limits.max_input_tokens_per_call
            ):
                return page
        return {
            "error": "REFERENCE_CONTEXT_LIMIT",
            "key": result["key"],
            "game_advanced": False,
            "message": "This decision has no room for another reference page; existing context is retained.",
        }

    def _exchange(self, raw, result):
        exchange = {"operation": raw if raw is not None else {"kind": "invalid"}, "result": result}
        if getattr(self.policy, "interface", "operate_v1") in ("tools_v2", "tools_v3", "tools_v4"):
            exchange["tool_call"] = getattr(self.policy, "last_tool_call", None)
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
        return feedback

    def run(self, *, eid=None, manifest=None, private=None, resume=None, history_prefix=None):
        if self.policy.paid:
            if (
                not self.limits.paid_calls_enabled
                or not self.limits.max_episode_cost_usd
                or not self.limits.max_batch_cost_usd
            ):
                raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
        self.rules = (
            restore_knowledge(self.store, resume)
            if resume
            else prepare_rules(self.rules, self.config.skills)
        )
        self.eid = eid or self.store.create(
            manifest
            or {
                "evidence_kind": self.game.evidence_kind,
                "config": self.config.public(),
                "agent": getattr(self.policy, "name", "model"),
                "evaluation_eligible": False,
            },
            private or {},
        )
        self.store.private_json(self.eid, "knowledge.json", self.rules)
        self.knowledge_reference = {"episode_id": self.eid, "hash": digest(self.rules)}
        self.history_prefix = history_prefix or []
        self.spending = self.spending or Spending(
            self.store.root / "private_runs" / self.eid / "spending.json",
            self.limits.max_batch_cost_usd,
        )
        start = 0
        self.prior_cost = 0.0
        if resume:
            self.game.restore(resume["game"])
            from balatro_horizons.engine.replay import check_private

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
                "environment_hash": digest(getattr(self.game, "lock", {"kind": "synthetic"})),
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
                        recent_events=self.recent[-20:],
                        remaining_budget=RemainingBudget(
                            game_actions=self.limits.max_game_actions - self.committed,
                            provider_calls=self.limits.max_provider_calls - self.calls,
                        ),
                    )
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
                    }
                    self.store.private_json(self.eid, f"checkpoint-{decision}.json", checkpoint)
                    self.log(
                        "checkpoint_result",
                        {"decision": decision, "saved": True, "certified": False},
                        observation_id=decision,
                    )
                except (NativeFailure, NativeRejected, OSError, ValueError):
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
                except NativeRejected:
                    self.log(
                        "action_rejected",
                        {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"},
                        observation_id=decision,
                        request_id=request_id,
                    )
                    outcome, reason = "INVALID_EVALUATION", "NATIVE_PUBLIC_LEGALITY_MISMATCH"
                    break
                self.committed += 1
                commit = self.log(
                    "action_commit",
                    envelope.model_dump(mode="json"),
                    actor=self.action_actor,
                    observation_id=decision,
                    request_id=request_id,
                )
                if hasattr(self.policy, "on_commit"):
                    self.policy.on_commit()
                if envelope.memory_update is not None:
                    self.memory = envelope.memory_update
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
                self.recent = self.recent[-20:]
                decision += 1
        except OperatorAbort:
            outcome, reason = "OPERATOR_ABORT", "OPERATOR_REQUEST"
        except BudgetExhausted as error:
            outcome, reason = "BUDGET_EXHAUSTED", str(error)
        except (NativeFailure, ProviderFailure) as error:
            outcome, reason = "INFRASTRUCTURE_FAILURE", str(error)
            self.log("action_status_unknown", {"code": reason})
        except Exception as error:
            # Exception text may contain private native state; only the type crosses.
            outcome, reason = "INFRASTRUCTURE_FAILURE", type(error).__name__
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
                    "cost_usd": self.cost,
                    "last_verified_observation_id": self.observation.observation_id
                    if self.observation
                    else None,
                },
            )
            try:
                self.game.close()
            except (NativeFailure, OSError):
                self.store.private_json(
                    self.eid, "cleanup-error.json", {"code": "NATIVE_CLEANUP_FAILED"}
                )
        return self.store.summary(self.eid)
