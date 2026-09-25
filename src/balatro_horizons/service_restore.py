"""Operator-confirmed admission to the existing single-game execution path."""

import fcntl
import re
from contextlib import contextmanager

from balatro_horizons.config import ROOT
from balatro_horizons.game.windows_context import load_session
from balatro_horizons.harness.money import Spending
from balatro_horizons.workbench.restoration import create_restoration, prepare_restore
from balatro_horizons.workbench.restore_ledger import restore_root


@contextmanager
def restore_admission(store, parent):
    root = restore_root(store, parent)
    path = store.episode_path(root, True) / "restore-admission.lock"
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("WORKER_BUSY") from None
        yield


def restore_preview(service, parent, *, paid_enabled):
    try:
        with service.admission():
            plan = prepare_restore(service.store, parent, paid_enabled=paid_enabled)
            service.validate_policy(plan["config"], plan["parent"]["agent"])
            return {"episode_id": parent, "available": True, "reason": None,
                    "plan": plan["public"]}
    except (OSError, ValueError, KeyError) as error:
        code = str(error) if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", str(error)) else "RESTORE_EVIDENCE_UNAVAILABLE"
        return {"episode_id": parent, "available": False, "reason": code, "plan": None}


def _runtime_preflight(store, plan):
    offline = plan["parent"]["evidence_kind"] == "SYNTHETIC_TEST"
    lock_path = store.root / "worker.lock" if offline else ROOT / "private/native-worker.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("WORKER_BUSY") from None
        if not offline:
            load_session()


def start_restore(service, parent, request, *, paid_enabled):
    with service.admission(), restore_admission(service.store, parent):
        plan = prepare_restore(service.store, parent, paid_enabled=paid_enabled)
        public, config = plan["public"], plan["config"]
        if request.parent_head != public["parent_head"] or request.plan_hash != public["plan_hash"]:
            raise ValueError("RESTORE_PLAN_CHANGED")
        if public["requires_paid_authorization"] and not request.authorize_paid:
            raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
        if ("uncapped" in public["limits"].values() and not request.confirm_uncapped):
            raise ValueError("UNCAPPED_CONFIRMATION_REQUIRED")
        if public["source_compatibility"] == "compatible_update" and not request.accept_compatible_update:
            raise ValueError("RESTORE_COMPATIBILITY_NOT_ACCEPTED")
        service.validate_policy(config, plan["parent"]["agent"])
        _runtime_preflight(service.store, plan)
        eid = create_restoration(service.store, parent, plan)
        service.stop.clear()
        service.error = None
        spending = Spending(plan["ledger"]["path"], config.budgets.max_batch_cost_usd)
        service.review.expose(eid, "operator_restore", model_identity_seen=True)
        service._launch(lambda: service.execute(
            config, plan["parent"]["agent"], service.store.manifest(parent, True)["seed"],
            offline=plan["parent"]["evidence_kind"] == "SYNTHETIC_TEST", eid=eid,
            resume=plan["resume"], prefix=plan["prefix"], spending=spending,
        ))
        return eid
