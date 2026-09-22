"""Execution preparation and locked worker lifecycle for :mod:`service`."""

import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from balatro_horizons.config import Config
from balatro_horizons.game.contract import GameSession
from balatro_horizons.harness.context.freeze import restore_protocol, validate_continuation
from balatro_horizons.harness.contract import Policy, ProviderPolicy
from balatro_horizons.harness.money import Spending
from balatro_horizons.storage.journal import digest


@dataclass(frozen=True)
class ExecutionPlan:
    config: Config
    agent: str
    seed: str
    offline: bool
    calibration: bool
    eid: str
    policy: Policy
    spending: Spending
    prompt_bytes: bytes | None
    resume: object
    prefix: object
    manifest: dict[str, Any]
    lock_path: Path


def prepare_execution(
    owner: Any,
    config: Config,
    agent: str,
    seed: str,
    *,
    offline: bool,
    calibration: bool,
    eid: str | None,
    extra: dict[str, Any] | None,
    resume: object,
    prefix: object,
    operations: list[object] | None,
    spending: Spending | None,
    human_steps: int,
    root: Path,
    load_session_fn: Callable[[], object],
) -> ExecutionPlan:
    config = config.model_copy(deep=True)
    if resume:
        validate_continuation(
            restore_protocol(owner.store, resume), config, agent, human=agent == "human"
        )
    policy = owner.policy(config, agent)
    from balatro_horizons.harness.instructions import load_prompt

    prompt_bytes = None if resume else load_prompt(root)
    if not offline and eid is None:
        load_session_fn()
    policy = owner._decorate_policy(policy, operations, human_steps)
    if calibration and (isinstance(policy, ProviderPolicy) or policy.model or agent == "human"):
        raise ValueError("CALIBRATION_REQUIRES_SCRIPTED_POLICY")
    manifest = _execution_manifest(
        config, agent, offline, calibration, resume, extra, operations, human_steps
    )
    eid = eid or owner.store.create(manifest, {"seed": seed, "config": config.model_dump()})
    spending = spending or Spending.episode_only(
        owner.store.root / "private_runs" / eid / "spending.json",
        config.budgets.max_batch_cost_usd,
    )
    owner.review.expose(eid, "operator_configuration", model_identity_seen=True)
    owner.active_id = eid
    lock_path = owner.store.root / "worker.lock" if offline else root / "private/native-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return ExecutionPlan(
        config=config,
        agent=agent,
        seed=seed,
        offline=offline,
        calibration=calibration,
        eid=eid,
        policy=policy,
        spending=spending,
        prompt_bytes=prompt_bytes,
        resume=resume,
        prefix=prefix,
        manifest=manifest,
        lock_path=lock_path,
    )


def _execution_manifest(
    config: Config,
    agent: str,
    offline: bool,
    calibration: bool,
    resume: object,
    extra: dict[str, Any] | None,
    operations: list[object] | None,
    human_steps: int,
) -> dict[str, Any]:
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
    return manifest


def execute_locked(
    owner: Any, plan: ExecutionPlan, *, run_episode_fn: Callable[..., object]
) -> object:
    game = None
    with plan.lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            game = owner.create_game(
                plan.config,
                plan.seed,
                offline=plan.offline,
                calibration=plan.calibration or bool(plan.resume),
            )
            rules = {"core": "See the shared rules kernel."}
            frozen = plan.lock_path.parent / "rules.json"
            if not plan.offline and frozen.exists():
                rules = json.loads(frozen.read_text())
                if rules.get("environment_hash") != digest(game.lock):
                    raise ValueError("FROZEN_RULES_ENVIRONMENT_MISMATCH")
            return run_episode_fn(
                owner.store,
                plan.config,
                game,
                plan.policy,
                plan.spending,
                stop=owner.stop,
                rules=rules,
                prompt_bytes=plan.prompt_bytes,
                eid=plan.eid,
                resume=plan.resume,
                history_prefix=plan.prefix,
            )
        except Exception as error:
            _finish_failed_execution(owner, plan, game, error)
            raise
        finally:
            owner.active_id = None


def _finish_failed_execution(
    owner: Any, plan: ExecutionPlan, game: GameSession | None, error: Exception
) -> None:
    if game:
        try:
            game.close()
        except Exception:
            owner.error = "NATIVE_CLEANUP_FAILED"
    if owner.store.summary(plan.eid):
        return
    owner.store.finish(
        plan.eid,
        {
            "episode_id": plan.eid,
            "evidence_kind": plan.manifest["evidence_kind"],
            "outcome": "INFRASTRUCTURE_FAILURE",
            "reason": str(error) if str(error).isupper() else type(error).__name__,
            "cost_usd": 0,
            "committed_actions": 0,
            "provider_calls": 0,
        },
    )
