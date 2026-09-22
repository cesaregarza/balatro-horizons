"""One-worker orchestration shared by the browser and CLI."""

import json
import os
import threading
from contextlib import contextmanager

from balatro_horizons.config import ROOT
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.session import NativeGame
from balatro_horizons.game.windows_context import load_session
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import run_episode
from balatro_horizons.harness.money import (
    Spending,
    batch_attempts,
    reconcile_stop,
    record_stop,
    validate_paid_configuration,
)
from balatro_horizons.harness.transport import DirectProvider
from balatro_horizons.service_execution import execute_locked, prepare_execution
from balatro_horizons.storage.journal import atomic_json, digest, identifier, locked
from balatro_horizons.workbench.branches import prepare_branch
from balatro_horizons.workbench.policies import (
    HumanPolicy,
    HumanSequencePolicy,
    InterventionPolicy,
)


class RunService:
    def __init__(self, store, review):
        self.store, self.review = store, review
        self.thread = None
        self.stop = threading.Event()
        self.human = None
        self.active_id = None
        self._guard = threading.Lock()
        self.error = None

    @contextmanager
    def admission(self):
        """Reserve the worker without making competing requests wait for replay."""
        if not self._guard.acquire(blocking=False):
            raise ValueError("WORKER_BUSY")
        try:
            if self.thread and self.thread.is_alive():
                raise ValueError("WORKER_BUSY")
            yield
        finally:
            self._guard.release()

    def validate_policy(self, config, agent):
        """Validate paid admission without constructing a client or game."""
        if agent in ("heuristic", "random_legal", "human"):
            return None
        if agent not in config.models:
            raise ValueError("UNKNOWN_AGENT_CONFIGURATION")
        model = config.models[agent]
        amount = validate_paid_configuration(model, config.budgets)
        key = "OPENAI_API_KEY" if model.provider == "openai" else "ANTHROPIC_API_KEY"
        if not os.environ.get(key):
            raise ValueError("MISSING_PROVIDER_CREDENTIAL")
        return amount

    def policy(self, config, agent):
        self.validate_policy(config, agent)
        if agent in ("heuristic", "random_legal"):
            return Baseline(agent)
        if agent == "human":
            self.human = HumanPolicy(self.stop)
            return self.human
        return DirectProvider(config.models[agent], config.budgets)

    def create_game(self, config, seed, *, offline=False, calibration=False):
        """Production ownership by default; calibration suites may supply a lease."""
        return (
            FakeGame(seed)
            if offline
            else NativeGame(config.environment, seed, calibration=calibration)
        )

    def _decorate_policy(self, policy, operations, human_steps):
        if operations:
            policy = InterventionPolicy(operations, policy)
        if human_steps:
            self.human = HumanPolicy(self.stop)
            policy = HumanSequencePolicy(self.human, policy, human_steps)
        return policy

    def execute(
        self,
        config,
        agent,
        seed,
        *,
        offline=False,
        calibration=False,
        eid=None,
        extra=None,
        resume=None,
        prefix=None,
        operations=None,
        spending=None,
        human_steps=0,
    ):
        plan = prepare_execution(
            self,
            config,
            agent,
            seed,
            offline=offline,
            calibration=calibration,
            eid=eid,
            extra=extra,
            resume=resume,
            prefix=prefix,
            operations=operations,
            spending=spending,
            human_steps=human_steps,
            root=ROOT,
            load_session_fn=load_session,
        )
        return execute_locked(self, plan, run_episode_fn=run_episode)

    def start(self, config, agent, seed, *, offline=False, calibration=False):
        config = config.model_copy(deep=True)
        with self.admission():
            self.stop.clear()
            self.error = None
            self.validate_policy(config, agent)
            from balatro_horizons.harness.instructions import load_prompt

            load_prompt(ROOT)
            if not offline:
                load_session()
            eid = self.store.create(
                {
                    "evidence_kind": "SYNTHETIC_TEST" if offline else "NATIVE",
                    "config": config.public(),
                    "agent": agent,
                    "evaluation_eligible": not offline and not calibration and agent != "human",
                    **({"assistance": "human_takeover"} if agent == "human" else {}),
                    "config_hash": digest(config.model_dump()),
                },
                {"seed": seed, "config": config.model_dump()},
            )
            self.review.expose(eid, "operator_launch", model_identity_seen=True)
            self._launch(
                lambda: self.execute(
                    config, agent, seed, offline=offline, calibration=calibration, eid=eid
                )
            )
            return eid

    def _launch(self, task):
        def target():
            try:
                task()
            except Exception as error:
                self.error = str(error) if str(error).isupper() else type(error).__name__

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()

    def branch(self, config, parent, decision, mode, actions=None, agent=None, human_steps=3):
        with self.admission():
            actions = actions or []
            if mode == "single_action_override" and len(actions) != 1:
                raise ValueError("ONE_OVERRIDE_REQUIRED")
            if (
                mode == "short_human_sequence"
                and not 1 <= (len(actions) if actions else human_steps) <= 20
            ):
                raise ValueError("INVALID_HUMAN_SEQUENCE")
            manifest = self.store.manifest(parent)
            chosen = "human" if mode == "human_takeover" else agent or manifest["agent"]
            if chosen not in ("human", manifest["agent"]):
                raise ValueError("PROTOCOL_CHANGE_INTERVENTION_NOT_SUPPORTED")
            self.validate_policy(config, chosen)  # Validate before immutable child records.
            if manifest["evidence_kind"] != "SYNTHETIC_TEST":
                load_session()
            eid, checkpoint, prefix = prepare_branch(self.store, config, parent, decision, mode)
            self.stop.clear()
            seed = self.store.manifest(parent, True)["seed"]
            self._launch(
                lambda: self.execute(
                    config,
                    chosen,
                    seed,
                    offline=manifest["evidence_kind"] == "SYNTHETIC_TEST",
                    eid=eid,
                    resume=checkpoint,
                    prefix=prefix,
                    operations=actions,
                    human_steps=human_steps
                    if mode == "short_human_sequence" and not actions
                    else 0,
                )
            )
            return eid

    def continue_budget(self, parent, combined_cap, *, expected_head):
        from balatro_horizons.workbench.budget_continuation import prepare_budget_continuation

        with self.admission():
            # This lock is shared across API/service processes. It spans the
            # ledger snapshot and child creation, so siblings cannot freeze the
            # same predecessor hash before either child becomes visible.
            admission = self.store.episode_path(parent, True) / "budget-admission.lock"
            with locked(admission):
                if self.store.manifest(parent)["evidence_kind"] != "SYNTHETIC_TEST":
                    load_session()
                plan = prepare_budget_continuation(
                    self.store, parent, combined_cap, expected_head=expected_head
                )
                config, manifest = plan["config"], plan["manifest"]
                amount = self.validate_policy(config, manifest["agent"])
                if amount is None:
                    raise ValueError("BUDGET_EXTENSION_REQUIRES_PAID_MODEL")
                if not plan["spending"].affordability(amount)[0]:
                    raise ValueError("BUDGET_EXTENSION_BELOW_RESERVATION")
                if plan["resume"]["calls"] >= config.budgets.max_provider_calls:
                    raise ValueError("PROVIDER_CALL_LIMIT")
                offline = manifest["evidence_kind"] == "SYNTHETIC_TEST"
                eid = self.store.create(manifest, plan["private"])
                self.stop.clear()
                self.error = None
                self.review.expose(eid, "operator_budget_extension", model_identity_seen=True)
                self._launch(lambda: self.execute(
                    config, manifest["agent"], plan["private"]["seed"], offline=offline,
                    eid=eid, resume=plan["resume"], prefix=plan["prefix"], spending=plan["spending"],
                ))
                return eid

    def run_batch(self, config, bid, *, offline=False):
        # Serialize invocations of this frozen campaign, including preflight and stops.
        with locked(self.store.root / "batches" / identifier(bid) / "scheduling.lock"):
            return self._run_batch(config, bid, offline=offline)

    def preflight_batch(self, config, bid, *, offline=False):
        with locked(self.store.root / "batches" / identifier(bid) / "scheduling.lock"):
            self._freeze_batch(config, bid, offline=offline)
            if not offline:
                load_session()

    def _freeze_batch(self, config, bid, *, offline):
        plan = json.loads((self.store.root / "batches" / bid / "plan.json").read_text())
        private = json.loads((self.store.root / "batches" / bid / "private.json").read_text())
        if plan["config_hash"] != digest(config.model_dump()):
            raise ValueError("BATCH_CONFIGURATION_CHANGED")
        execution_path = self.store.root / "batches" / bid / "execution.json"
        evidence = "SYNTHETIC_TEST" if offline else "NATIVE"
        if execution_path.exists():
            if json.loads(execution_path.read_text())["evidence_kind"] != evidence:
                raise ValueError("BATCH_EVIDENCE_KIND_CHANGED")
        else:
            atomic_json(execution_path, {"evidence_kind": evidence}, immutable=True)
        return plan, private

    def _run_batch(self, config, bid, *, offline):
        plan, private = self._freeze_batch(config, bid, offline=offline)
        spending = Spending(
            self.store.root / "batches" / bid / "spending.json", config.budgets.max_batch_cost_usd
        )
        originals = batch_attempts(self.store, plan)
        if reconcile_stop(self.store, plan, originals) is not None:
            return bid
        for slot in plan["slots"]:
            if self.stop.is_set():
                break
            attempts = [r for r in originals if r["manifest"]["slot_id"] == slot["slot_id"]]
            while len(attempts) < 2 and (
                not attempts
                or all(
                    (r["summary"] or {}).get("outcome") == "INFRASTRUCTURE_FAILURE"
                    for r in attempts
                )
            ):
                if self.stop.is_set():
                    break
                amount = self.validate_policy(config, slot["agent"])
                if amount is not None:
                    affordable, context = spending.affordability(amount)
                    if not affordable:
                        record_stop(
                            self.store,
                            plan,
                            reason="CAMPAIGN_COST_CAP",
                            stage="preflight",
                            slot_id=slot["slot_id"],
                            agent=slot["agent"],
                            cost_context=context,
                        )
                        return bid
                if not offline:
                    load_session()
                try:
                    self.execute(
                        config,
                        slot["agent"],
                        private["seed_by_group"][slot["seed_group"]],
                        offline=offline,
                        extra={
                            "batch_id": bid,
                            "slot_id": slot["slot_id"],
                            "seed_group": slot["seed_group"],
                            "replicate": slot["replicate"],
                            "evaluation_eligible": True,
                            "synthetic_accounting_only": offline,
                        },
                        spending=spending,
                    )
                except Exception:
                    current = [
                        r
                        for r in batch_attempts(self.store, plan)
                        if r["manifest"]["slot_id"] == slot["slot_id"]
                    ]
                    if len(current) == len(attempts):
                        raise
                originals = batch_attempts(self.store, plan)
                if reconcile_stop(self.store, plan, originals, recovered=False) is not None:
                    return bid
                attempts = [r for r in originals if r["manifest"]["slot_id"] == slot["slot_id"]]
        return bid
