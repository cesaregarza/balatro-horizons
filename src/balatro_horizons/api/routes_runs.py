"""Run launch and operator status routes."""

import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from balatro_horizons.config import ModelConfig
from balatro_horizons.game.windows_context import connection_status

from .middleware import require_operator
from .models import RunInput

router = APIRouter()


@router.get("/api/operator/runtime", dependencies=[Depends(require_operator)])
def operator_runtime():
    return connection_status()


def new_seed():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


@router.post("/api/runs", dependencies=[Depends(require_operator)])
def start_run(request: Request, data: RunInput):
    state = request.app.state
    chosen = state.config.model_copy(deep=True)
    agent = _configured_agent(state.config.models, data.agent)
    if data.model_settings is not None:
        model = chosen.models.get(agent)
        if model is None:
            raise ValueError("MODEL_SETTINGS_REQUIRE_CONFIGURED_MODEL")
        configured = model.model_dump()
        configured["settings"] = {**model.settings, **data.model_settings}
        try:
            chosen.models[agent] = ModelConfig.model_validate(configured)
        except ValidationError as error:
            raise ValueError("INVALID_MODEL_SETTINGS") from error
    if data.preset == "smoke":
        chosen.environment.stake = "WHITE"
    if data.cost_override is not None:
        chosen.budgets.max_episode_cost_usd = data.cost_override
        chosen.budgets.max_batch_cost_usd = data.cost_override
    if "uncapped" in (chosen.budgets.max_episode_cost_usd, chosen.budgets.max_batch_cost_usd):
        if not data.confirm_uncapped:
            raise ValueError("UNCAPPED_CONFIRMATION_REQUIRED")
    return {
        "episode_id": state.runs.start(
            chosen,
            agent,
            data.seed or new_seed(),
            offline=data.offline,
            calibration=data.calibration,
        )
    }


def _configured_agent(models, requested):
    if requested in models or not requested.startswith("model:"):
        return requested
    matches = [
        key for key, model in models.items()
        if requested == f"model:{model.provider}:{quote(model.model, safe='')}"
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError("MODEL_NOT_CONFIGURED" if not matches else "AMBIGUOUS_CONFIGURED_MODEL")


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
    }
