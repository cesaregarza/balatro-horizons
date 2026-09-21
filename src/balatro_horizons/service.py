"""One-worker orchestration shared by the browser and CLI."""

import fcntl
import json
import os
import queue
import threading

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.budget import Spending, validate_paid_configuration
from balatro_horizons.agents.providers import DirectProvider
from balatro_horizons.config import ROOT
from balatro_horizons.evaluation.scheduling import batch_attempts, reconcile_stop, record_stop
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.session import NativeGame
from balatro_horizons.harness.context.freeze import restore_protocol, validate_continuation
from balatro_horizons.harness.contract import ProviderPolicy
from balatro_horizons.review.branches import prepare_branch
from balatro_horizons.runner import OperatorAbort, Runner
from balatro_horizons.storage.journal import atomic_json, digest, identifier, locked


class HumanPolicy:
    interface = "tools_v7"
    name = "human"
    actor = "human"
    model = None

    def __init__(self, stop):
        self.queue = queue.Queue(maxsize=1)
        self.current = None
        self.stop = stop

    def decide(self, ctx, exchanges):
        self.current = {"context": ctx, "exchanges": exchanges}
        while not self.stop.is_set():
            try:
                result = self.queue.get(timeout=0.25)
                self.current = None
                return result
            except queue.Empty:
                pass
        raise OperatorAbort

    def on_decision_end(self) -> None:
        # The submitted operation already left the one-slot human queue.
        pass

    def on_commit(self) -> None:
        # A human selection has no provider continuation to advance.
        pass


class InterventionPolicy:
    interface = "tools_v7"

    def __init__(self, operations, continuation):
        self.operations = list(operations)
        self.continuation = continuation

    @property
    def actor(self):
        return "human_override" if self.operations else self.continuation.actor

    @property
    def active_policy(self):
        return self if self.operations else self.continuation

    @property
    def name(self):
        return self.continuation.name

    @property
    def model(self):
        return self.continuation.model

    def decide(self, ctx, exchanges):
        if self.operations:
            return {
                "kind": "action",
                "envelope": {
                    "observation_id": ctx.observation["observation_id"],
                    "action": self.operations.pop(0),
                },
            }
        return self.continuation.decide(ctx, exchanges)

    def on_decision_end(self):
        # An override itself has no continuation; forwarded turns have their own owner.
        pass

    def on_commit(self):
        # Removing the operation at decision time already advances this wrapper.
        pass


class HumanSequencePolicy:
    interface = "tools_v7"

    def __init__(self, human, continuation, steps):
        self.human, self.continuation, self.remaining = human, continuation, steps

    @property
    def active_policy(self):
        return self.human if self.remaining else self.continuation

    @property
    def name(self):
        return self.continuation.name

    @property
    def model(self):
        return self.continuation.model

    @property
    def actor(self):
        return "human" if self.remaining else self.continuation.actor

    def decide(self, ctx, exchanges):
        return (self.human if self.remaining else self.continuation).decide(ctx, exchanges)

    def on_commit(self):
        self.remaining = max(0, self.remaining - 1)

    def on_decision_end(self):
        # The active human/provider policy clears its own state before this wrapper advances.
        pass


class RunService:
    def __init__(self, store, review):
        self.store, self.review = store, review
        self.thread = None
        self.stop = threading.Event()
        self.human = None
        self.active_id = None
        self._guard = threading.Lock()
        self.error = None

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
        config = config.model_copy(deep=True)
        if resume:
            validate_continuation(
                restore_protocol(self.store, resume), config, agent, human=agent == "human"
            )
        policy = self.policy(config, agent)
        from balatro_horizons.agents.instructions import load_prompt

        # NativeGame's constructor launches the game. Validate and capture prompt
        # bytes before constructing it; ordinary branches use their original snapshot.
        prompt_bytes = None if resume else load_prompt(ROOT)
        if operations:
            policy = InterventionPolicy(operations, policy)
        if human_steps:
            self.human = HumanPolicy(self.stop)
            policy = HumanSequencePolicy(self.human, policy, human_steps)
        if calibration and (isinstance(policy, ProviderPolicy) or policy.model or agent == "human"):
            raise ValueError("CALIBRATION_REQUIRES_SCRIPTED_POLICY")
        manifest = {
            "evidence_kind": "SYNTHETIC_TEST" if offline else "NATIVE",
            "config": config.public(),
            "agent": agent,
            "evaluation_eligible": not offline and not calibration and not resume,
            "config_hash": digest(config.model_dump()),
            **(extra or {}),
        }
        if agent == "human" or resume or operations or human_steps:
            manifest["evaluation_eligible"] = False
            manifest["assistance"] = "human_takeover" if agent == "human" else "intervention"
        eid = eid or self.store.create(manifest, {"seed": seed, "config": config.model_dump()})
        self.review.expose(eid, "operator_configuration", model_identity_seen=True)
        self.active_id = eid
        lock_path = (
            self.store.root / "worker.lock" if offline else ROOT / "private/native-worker.lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        game = None
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                game = self.create_game(
                    config, seed, offline=offline, calibration=calibration or bool(resume)
                )
                rules = {"core": "See the shared rules kernel."}
                frozen = ROOT / "private/rules.json"
                if not offline and frozen.exists():
                    rules = json.loads(frozen.read_text())
                    if rules.get("environment_hash") != digest(game.lock):
                        raise ValueError("FROZEN_RULES_ENVIRONMENT_MISMATCH")
                return Runner(
                    self.store, config, game, policy, stop=self.stop, spending=spending, rules=rules,
                    prompt_bytes=prompt_bytes,
                ).run(eid=eid, resume=resume, history_prefix=prefix)
            except Exception as error:
                if game:
                    try:
                        game.close()
                    except Exception:
                        self.error = "NATIVE_CLEANUP_FAILED"
                if not self.store.summary(eid):
                    self.store.finish(
                        eid,
                        {
                            "episode_id": eid,
                            "evidence_kind": manifest["evidence_kind"],
                            "outcome": "INFRASTRUCTURE_FAILURE",
                            "reason": str(error) if str(error).isupper() else type(error).__name__,
                            "cost_usd": 0,
                            "committed_actions": 0,
                            "provider_calls": 0,
                        },
                    )
                raise
            finally:
                self.active_id = None

    def start(self, config, agent, seed, *, offline=False, calibration=False):
        config = config.model_copy(deep=True)
        with self._guard:
            if self.thread and self.thread.is_alive():
                raise ValueError("WORKER_BUSY")
            self.stop.clear()
            self.error = None
            self.validate_policy(config, agent)
            from balatro_horizons.agents.instructions import load_prompt

            load_prompt(ROOT)
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
        with self._guard:
            if self.thread and self.thread.is_alive():
                raise ValueError("WORKER_BUSY")
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

    def run_batch(self, config, bid, *, offline=False):
        # Serialize invocations of this frozen campaign, including preflight and stops.
        with locked(self.store.root / "batches" / identifier(bid) / "scheduling.lock"):
            return self._run_batch(config, bid, offline=offline)

    def _run_batch(self, config, bid, *, offline):
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
