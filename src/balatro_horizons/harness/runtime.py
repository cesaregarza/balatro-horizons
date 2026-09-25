"""Episode state, persistence, and the observe/commit runtime."""

import json
import threading
import uuid
from copy import deepcopy

from balatro_horizons.config import RECENT_PUBLIC_EVENT_LIMIT
from balatro_horizons.contracts import Observation, RecentPublicEvent, RemainingBudget
from balatro_horizons.evidence.provenance import (
    continuation_fingerprint,
    implementation_fingerprint,
)
from balatro_horizons.game.contract import (
    GameSession,
    NativeFailure,
    NativeRejected,
)
from balatro_horizons.harness.context.freeze import freeze_protocol, restore_protocol
from balatro_horizons.harness.context.memory import (
    RunNotebook,
    WorkingMemory,
    restore_notebook,
    restore_working_memory,
)
from balatro_horizons.harness.context.references import ModelReferences
from balatro_horizons.harness.context.render import KERNEL
from balatro_horizons.harness.contract import (
    EnvironmentLockedGame,
    NamedPolicy,
    Policy,
    RoutedPolicy,
)
from balatro_horizons.harness.decision import DecisionRuntimeMixin
from balatro_horizons.harness.money import BudgetExhausted, Spending, validate_paid_configuration
from balatro_horizons.harness.provider import OperatorAbort
from balatro_horizons.harness.runtime_diagnostics import RuntimeDiagnosticsMixin
from balatro_horizons.harness.skills import prepare_rules, restore_knowledge
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.observations.projection import HandleIssuer, project_public
from balatro_horizons.storage.journal import digest


class Runner(RuntimeDiagnosticsMixin, DecisionRuntimeMixin):
    """Run one episode with an explicitly supplied spending ledger."""

    def __init__(
        self,
        store,
        config,
        game: GameSession,
        policy: Policy,
        spending,
        *,
        stop=None,
        rules=None,
        prompt_bytes=None,
    ):
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
        self.model_references = ModelReferences()
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
        self.model_references.consume(event)
        return event

    def bind_ledger(self):
        """Require an explicit ledger; callers choose campaign or episode scope."""
        if not isinstance(self.spending, Spending):
            raise TypeError("SPENDING_LEDGER_REQUIRED")
        return self.spending

    def freeze_or_restore_protocol(self, resume):
        """Freeze the protocol once, or validate the immutable resume contract."""
        self.execution_implementation_hash = implementation_fingerprint()
        if isinstance(self.policy, Policy) and self.policy.model is not None:
            validate_paid_configuration(self.policy.model, self.limits)
        self.rules = restore_knowledge(self.store, resume) if resume else prepare_rules(
            self.rules, self.config.skills
        )
        self.protocol = restore_protocol(self.store, resume) if resume else freeze_protocol(
            self.config, self.policy, self.rules, prompt_bytes=self.prompt_bytes
        )
        self._validate_resume_protocol(resume)
        if self.protocol["knowledge_hash"] != digest(self.rules):
            raise ValueError("AGENT_PROTOCOL_KNOWLEDGE_CHANGED")
        self.source_compatibility = deepcopy(resume.get("source_compatibility")) if resume else None

    def _validate_resume_protocol(self, resume):
        if not resume:
            return
        from balatro_horizons.harness.context.freeze import episode_limits

        if self.protocol["episode_limits"] != episode_limits(self.config):
            raise ValueError("AGENT_PROTOCOL_CONFIGURATION_CHANGED")
        model = self.policy.model if isinstance(self.policy, Policy) else None
        if self.policy.name != "human" and self.protocol["model"] != (
            model.model_dump() if model is not None else None
        ):
            raise ValueError("AGENT_PROTOCOL_MODEL_CHANGED")

    def create_episode(self, *, eid=None, manifest=None, private=None, history_prefix=None):
        """Create the public episode and persist immutable private references."""
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
        for event in self.history_prefix:
            self.model_references.consume(event)

    def rehydrate_resume_state(self, resume):
        """Restore durable notebook, game, and counters before the first decision."""
        self.prior_cost = 0.0
        self.start = 0
        self.previous_action = None
        if not resume:
            return
        if not resume.get("continuation_hash"):
            raise ValueError("CHECKPOINT_CONTINUATION_MISSING")
        self.notebook = restore_notebook(
            resume.get("run_notebook"), self.history_prefix, self.limits.memory_max_characters
        )
        self.working_memory = restore_working_memory(
            resume.get("working_memory"), self.history_prefix, resume["observation"]
        )
        self.game.restore(resume["game"])
        from balatro_horizons.game.replay import check_private

        check_private(self.game, resume.get("continuation_hash"))
        self.issuer = HandleIssuer.restore(resume["issuer"])
        self.prior_cost = resume.get("cost", 0.0)
        self.memory = resume["memory"]
        self.committed = resume["committed"]
        self.calls = resume["calls"]
        self.recent = [RecentPublicEvent.model_validate(item) for item in resume["recent"]]
        self.start = resume["observation"]["observation_id"]

    def play(self):
        """Run the observe/checkpoint/decide/commit cycle until terminal."""
        self._log_episode_start()
        decision = self.start
        while True:
            self._check_play_stop()
            result = self._play_decision(decision)
            if result is not None:
                return result
            decision += 1

    def _log_episode_start(self):
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

    def _check_play_stop(self):
        if self.stop.is_set():
            raise OperatorAbort

    def _play_decision(self, decision):
        raw, obs_event = self._observe_decision(decision)
        terminal = self.game.terminal_status()
        if terminal:
            return terminal, "VERIFIED_ENGINE_TERMINAL"
        if self.committed >= self.limits.max_game_actions:
            raise BudgetExhausted("GAME_ACTION_LIMIT")
        self._write_checkpoint(decision, raw, obs_event)
        envelope, failure = self._decision(self.observation)
        if failure:
            return failure, failure
        return self._commit_action(envelope, decision, obs_event)

    def _observe_decision(self, decision):
        self.game.wait_ready()
        raw = self.game.observe_private()
        self.store.private_json(self.eid, f"raw-{decision}.json", raw)
        if self.resume and decision == self.start:
            self.observation = Observation.model_validate(self.resume["observation"])
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
        if self.previous_action is not None:
            previous, action = self.previous_action
            self.observation.last_action = last_action(previous, self.observation, action)
        return raw, self.log(
            "observation", self.observation.model_dump(mode="json"), observation_id=decision
        )

    def _write_checkpoint(self, decision, raw, obs_event):
        try:
            checkpoint = self._checkpoint_payload(raw, obs_event)
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

    def _checkpoint_payload(self, raw, obs_event):
        return {
            "game": self.game.checkpoint(),
            "knowledge": self.knowledge_reference,
            "agent_protocol": deepcopy(self.protocol_reference),
            "issuer": self.issuer.snapshot(),
            "source_compatibility": deepcopy(self.source_compatibility),
            "observation": self.observation.model_dump(mode="json"),
            "memory": self.memory,
            "committed": self.committed,
            "calls": self.calls,
            "cost": self.prior_cost + self.cost,
            "implementation_hash": implementation_fingerprint(),
            "continuation_hash": continuation_fingerprint(raw),
            "recent": [event.model_dump() for event in self.recent],
            "public_prefix_hash": obs_event["hash"],
            "run_notebook": self.notebook.snapshot(),
            "working_memory": self.working_memory.view(),
        }

    def _commit_action(self, envelope, decision, obs_event):
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
            return self._reject_native_action(error, request_id)
        self.committed += 1
        self.previous_action = (self.observation, envelope.action)
        commit = self.log(
            "action_commit",
            envelope.model_dump(mode="json"),
            actor=self.action_actor,
            observation_id=decision,
            request_id=request_id,
        )
        self._finish_policy_turn(commit, obs_event, envelope)
        return None

    def _reject_native_action(self, error, request_id):
        self._record_native_error(error, "action")
        self.log(
            "action_rejected",
            {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"},
            observation_id=self.observation.observation_id,
            request_id=request_id,
        )
        return "INVALID_EVALUATION", self._native_code(error, "NATIVE_PUBLIC_LEGALITY_MISMATCH")

    def _finish_policy_turn(self, commit, obs_event, envelope):
        if isinstance(self.active_policy, Policy):
            self.active_policy.on_decision_end()
        if isinstance(self.policy, Policy):
            self.policy.on_commit()
        self.recent.extend([
            RecentPublicEvent(
                event_id=obs_event["event_id"],
                event_type="observation",
                summary=json.dumps({
                    "phase": self.observation.phase,
                    "progress": self.observation.state.progress.model_dump(),
                    "resources": self.observation.state.resources.model_dump(),
                }),
            ),
            RecentPublicEvent(
                event_id=commit["event_id"],
                event_type="action_commit",
                summary=json.dumps(envelope.action.model_dump(mode="json")),
            ),
        ])
        self.recent = self.recent[-RECENT_PUBLIC_EVENT_LIMIT:]

    def finish(self, outcome, reason, cost_context=None):
        """Write the terminal journal record and always close the game session."""
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
                "last_verified_observation_id": (
                    self.observation.observation_id if self.observation else None
                ),
            },
        )
        try:
            self.game.close()
        except (NativeFailure, OSError) as error:
            if isinstance(error, NativeFailure):
                self._record_native_error(error, "cleanup")
            self.store.private_json(self.eid, "cleanup-error.json", {"code": "NATIVE_CLEANUP_FAILED"})
