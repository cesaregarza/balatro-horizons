"""Bootstrap and operator configuration routes."""

import os

from fastapi import APIRouter, Depends, Request

from balatro_horizons.config import Config
from balatro_horizons.storage.journal import atomic_json

from .middleware import require_operator
from .models import SettingsInput

router = APIRouter()


@router.get("/api/bootstrap")
def bootstrap(request: Request):
    """The token is acceptable here because the supported bind is loopback."""
    config = request.app.state.config
    return {
        "operator_token": request.app.state.operator_token,
        "config": config.public(),
        "workbench": config.workbench_enabled,
        "paid_credentials": {
            name: bool(os.environ.get(key))
            for name, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"))
        },
    }


@router.put("/api/settings", dependencies=[Depends(require_operator)])
def update_settings(request: Request, data: SettingsInput):
    state = request.app.state
    if state.runs.thread and state.runs.thread.is_alive():
        raise ValueError("WORKER_BUSY")
    state.config = Config.model_validate(
        {**state.config.model_dump(), **data.model_dump(exclude_unset=True)}
    )
    atomic_json(state.settings_path, state.config.model_dump())
    return state.config.public()
