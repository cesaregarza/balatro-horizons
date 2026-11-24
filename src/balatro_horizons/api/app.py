"""FastAPI application assembly for the dashboard gateway."""

import json
import secrets

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.review.operator_library import OperatorLibrary
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store

from . import (
    routes_annotate,
    routes_batches,
    routes_cloud,
    routes_explore,
    routes_runs,
    routes_settings,
)
from .middleware import ensure_loopback, install_middleware, validate_public_origin


def create_app(
    data_dir=None,
    config=None,
    *,
    public_origin=None,
    bind_host="127.0.0.1",
    output_root=None,
    workbench_enabled=None,
    view_only=False,
):
    ensure_loopback(bind_host)
    store = Store(data_dir or ROOT / "data")
    cfg, settings_path = load_runtime_config(store, config, workbench_enabled)
    if view_only and cfg.workbench_enabled:
        raise ValueError("VIEW_ONLY_EXCLUDES_WORKBENCH")
    app = FastAPI(title="Balatro Horizons", docs_url=None, redoc_url=None, openapi_url=None)
    review = ReviewService(store)
    workbench = None
    if cfg.workbench_enabled:
        from balatro_horizons.workbench.service import WorkbenchService

        workbench = WorkbenchService(store)
    install_state(app, store, review, workbench, cfg, settings_path, output_root, view_only)
    allowed_hosts = ["127.0.0.1", "localhost", "testserver"]
    public_host, public_authority = validate_public_origin(public_origin, allowed_hosts)
    install_middleware(app, allowed_hosts, public_origin, public_host, public_authority)
    include_routes(app, cfg.workbench_enabled, view_only)
    mount_static(app, app.state.output_root)
    return app


def load_runtime_config(store, config, workbench_enabled):
    cfg = config or load_config()
    settings_path = store.root / "operator-settings.json"
    if settings_path.exists():
        from balatro_horizons.cost_limits import require_capped_defaults

        cfg = Config.model_validate(json.loads(settings_path.read_text()))
        require_capped_defaults(cfg.budgets.max_episode_cost_usd, cfg.budgets.max_batch_cost_usd)
    if workbench_enabled is not None:
        cfg = cfg.model_copy(update={"workbench_enabled": workbench_enabled})
    return cfg, settings_path


def install_state(app, store, review, workbench, config, settings_path, output_root, view_only):
    app.state.store = store
    app.state.review = review
    app.state.workbench = workbench
    app.state.runs = RunService(store, review)
    app.state.operator_status = OperatorStatus(store, review)
    app.state.operator_library = OperatorLibrary(store, review)
    app.state.config = config
    app.state.settings_path = settings_path
    app.state.output_root = output_root or ROOT
    app.state.view_only = view_only
    app.state.operator_token = secrets.token_hex(32)


def include_routes(app, workbench_enabled, view_only):
    modules = (routes_runs, routes_explore, routes_settings, routes_batches, routes_annotate,
               routes_cloud)
    for module in modules:
        app.include_router(module.router)
    if not view_only:
        # What the operator does rather than reads: start or stop a run, save settings,
        # plan and run batches, annotate.
        for module in (routes_runs, routes_settings, routes_batches, routes_annotate):
            app.include_router(module.actions)
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
