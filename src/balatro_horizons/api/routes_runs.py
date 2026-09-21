"""Run launch and operator status routes."""

import secrets

from fastapi import APIRouter, Depends, Request

from .middleware import require_operator
from .models import RunInput

router = APIRouter()


def new_seed():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


@router.post("/api/runs", dependencies=[Depends(require_operator)])
def start_run(request: Request, data: RunInput):
    state = request.app.state
    chosen = state.config.model_copy(deep=True)
    if data.preset == "smoke":
        chosen.environment.stake = "WHITE"
    return {
        "episode_id": state.runs.start(
            chosen,
            data.agent,
            data.seed or new_seed(),
            offline=data.offline,
            calibration=data.calibration,
        )
    }


@router.post("/api/stop", dependencies=[Depends(require_operator)])
def stop_run(request: Request):
    request.app.state.runs.stop.set()
    return {"stop_requested": True}


@router.get("/api/operator/status", dependencies=[Depends(require_operator)])
def operator_status(request: Request):
    state = request.app.state
    return {
        "running": bool(state.runs.thread and state.runs.thread.is_alive()),
        "active_episode": state.runs.active_id,
        "error": state.runs.error,
        "episodes": state.operator_status.episodes(),
        "runtime_connection": runtime_status(),
    }


@router.get("/api/operator/runtime", dependencies=[Depends(require_operator)])
def runtime_connection():
    return runtime_status()


def runtime_status():
    """Report the safe default until the separately certified native bridge is up."""
    return {
        "ready": False,
        "code": "NATIVE_RUNTIME_UNAVAILABLE",
        "message": "Native runtime is unavailable; synthetic episodes remain available.",
    }
