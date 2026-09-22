"""Execution preparation and locked worker lifecycle for :mod:`service`."""

import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Protocol

from balatro_horizons.config import Config
from balatro_horizons.game.contract import GameSession
from balatro_horizons.harness.contract import Policy, ProviderPolicy
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.terminals import INCOMPLETE_TERMINAL_REASON
from balatro_horizons.storage.journal import Store, digest


@dataclass(frozen=True)
class ExecutionRequest:
    config: Config
    agent: str
    seed: str
    offline: bool
    eid: str | None
    resume: dict[str, Any] | None
    prefix: list[dict[str, Any]] | None
    spending: Spending | None


@dataclass(frozen=True)
class ExecutionPlan:
    request: ExecutionRequest
    calibration: bool
    eid: str
    policy: Policy
    spending: Spending
    prompt_bytes: bytes | None
    evidence_kind: str
    lock_path: Path
    rules_path: Path


class ExecutionOwner(Protocol):
    store: Store
    stop: Event
    active_id: str | None
    error: str | None

    def create_game(self, config: Config, seed: str, *, offline: bool,
                    calibration: bool) -> GameSession: ...


def prepare_execution(
    store: Store,
    request: ExecutionRequest,
    policy: Policy,
    prompt_bytes: bytes | None,
    *,
    calibration: bool,
    extra: dict[str, Any] | None,
    assisted: bool,
    root: Path,
) -> ExecutionPlan:
    config = request.config
    if calibration and (isinstance(policy, ProviderPolicy) or policy.model or request.agent == "human"):
        raise ValueError("CALIBRATION_REQUIRES_SCRIPTED_POLICY")
    manifest = _execution_manifest(request, calibration, extra, assisted)
    eid = request.eid or store.create(manifest, {"seed": request.seed, "config": config.model_dump()})
    spending = request.spending or Spending.episode_only(
        store.root / "private_runs" / eid / "spending.json",
        config.budgets.max_batch_cost_usd,
    )
    return ExecutionPlan(
        request=request,
        calibration=calibration or bool(request.resume),
        eid=eid,
        policy=policy,
        spending=spending,
        prompt_bytes=prompt_bytes,
        evidence_kind=manifest["evidence_kind"],
        lock_path=store.root / "worker.lock" if request.offline else root / "private/native-worker.lock",
        rules_path=root / "private/rules.json",
    )


def _execution_manifest(
    request: ExecutionRequest,
    calibration: bool,
    extra: dict[str, Any] | None,
    assisted: bool,
) -> dict[str, Any]:
    config = request.config
    manifest = {
        "evidence_kind": "SYNTHETIC_TEST" if request.offline else "NATIVE",
        "config": config.public(),
        "agent": request.agent,
        "evaluation_eligible": not request.offline and not calibration and not request.resume,
        "config_hash": digest(config.model_dump()),
        **(extra or {}),
    }
    if request.agent == "human" or request.resume or assisted:
        manifest["evaluation_eligible"] = False
        manifest["assistance"] = "human_takeover" if request.agent == "human" else "intervention"
    return manifest


def execute_locked(
    owner: ExecutionOwner, plan: ExecutionPlan, *, run_episode_fn: Callable[..., object]
) -> object:
    game = None
    request = plan.request
    with plan.lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            game = owner.create_game(
                request.config,
                request.seed,
                offline=request.offline,
                calibration=plan.calibration,
            )
            rules = {"core": "See the shared rules kernel."}
            if not request.offline and plan.rules_path.exists():
                rules = json.loads(plan.rules_path.read_text())
                if rules.get("environment_hash") != digest(game.lock):
                    raise ValueError("FROZEN_RULES_ENVIRONMENT_MISMATCH")
            return run_episode_fn(
                owner.store,
                request.config,
                game,
                plan.policy,
                plan.spending,
                stop=owner.stop,
                rules=rules,
                prompt_bytes=plan.prompt_bytes,
                eid=plan.eid,
                resume=request.resume,
                history_prefix=request.prefix,
            )
        except Exception as error:
            _finish_failed_execution(owner, plan, game, error)
            raise
        finally:
            owner.active_id = None


def _finish_failed_execution(
    owner: ExecutionOwner, plan: ExecutionPlan, game: GameSession | None, error: Exception
) -> None:
    if game:
        try:
            game.close()
        except Exception:
            owner.error = "NATIVE_CLEANUP_FAILED"
    if owner.store.summary(plan.eid):
        return
    reason = str(error) if str(error).isupper() else type(error).__name__
    if any(event["type"] == "episode_start" for event in owner.store.events(plan.eid)):
        # Zeroes are placeholders, not reconstructed action or spend totals.
        reason = INCOMPLETE_TERMINAL_REASON
    owner.store.finish(
        plan.eid,
        {
            "episode_id": plan.eid,
            "evidence_kind": plan.evidence_kind,
            "outcome": "INFRASTRUCTURE_FAILURE",
            "reason": reason,
            "cost_usd": 0,
            "committed_actions": 0,
            "provider_calls": 0,
        },
    )
