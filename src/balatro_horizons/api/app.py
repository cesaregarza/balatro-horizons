"""FastAPI application assembly for the dashboard gateway."""

import json
import secrets

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store

from .middleware import ensure_loopback, install_middleware, validate_public_origin
from .routes_explore import router as explore_router
from .routes_runs import router as runs_router
from .routes_settings import router as settings_router


def create_app(
    data_dir=None,
    config=None,
    *,
    public_origin=None,
    bind_host="127.0.0.1",
    allow_remote=False,
    output_root=None,
    workbench_enabled=None,
):
    ensure_loopback(bind_host, allow_remote)
    store = Store(data_dir or ROOT / "data")
    cfg, settings_path = load_runtime_config(store, config, workbench_enabled)
    app = FastAPI(title="Balatro Horizons", docs_url=None, redoc_url=None, openapi_url=None)
    review = ReviewService(store)
    workbench = None
    if cfg.workbench_enabled:
        from balatro_horizons.workbench.service import WorkbenchService

        workbench = WorkbenchService(store)
    install_state(app, store, review, workbench, cfg, settings_path, output_root)
    allowed_hosts = ["127.0.0.1", "localhost", "testserver"]
    public_host, public_authority = validate_public_origin(public_origin, allowed_hosts)
    install_middleware(app, allowed_hosts, public_origin, public_host, public_authority)
    include_routes(app, cfg.workbench_enabled)
    mount_static(app, app.state.output_root)
    return app


def load_runtime_config(store, config, workbench_enabled):
    cfg = config or load_config()
    settings_path = store.root / "operator-settings.json"
    if settings_path.exists():
        cfg = Config.model_validate(json.loads(settings_path.read_text()))
    if workbench_enabled is not None:
        cfg = cfg.model_copy(update={"workbench_enabled": workbench_enabled})
    return cfg, settings_path


def install_state(app, store, review, workbench, config, settings_path, output_root):
    app.state.store = store
    app.state.review = review
    app.state.workbench = workbench
    app.state.runs = RunService(store, review)
    app.state.operator_status = OperatorStatus(store, review)
    app.state.config = config
    app.state.settings_path = settings_path
    app.state.output_root = output_root or ROOT
    app.state.operator_token = secrets.token_hex(32)


def include_routes(app, workbench_enabled):
    app.include_router(runs_router)
    app.include_router(explore_router)
    app.include_router(settings_router)
    from .routes_annotate import router as annotate_router
    from .routes_batches import router as batches_router

    app.include_router(batches_router)
    app.include_router(annotate_router)
    if not workbench_enabled:
        return
    from balatro_horizons.workbench.routes import router as workbench_router

    app.include_router(workbench_router)


def mount_static(app, root):
    dist = root / "web/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").exists():
            from fastapi import HTTPException

            raise HTTPException(503, "Build the browser application first.")
        return FileResponse(dist / "index.html")
