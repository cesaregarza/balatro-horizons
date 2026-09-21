"""Seed panels, batch execution, reporting, and public exports."""

import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from balatro_horizons.evaluation.batches import plan_batch, seed_panel
from balatro_horizons.evaluation.reports import export_batch, report_batch
from balatro_horizons.storage.journal import identifier

from .middleware import require_operator
from .models import BatchInput, BatchRun, PanelInput

router = APIRouter()


@router.get("/api/panels", dependencies=[Depends(require_operator)])
def panels(request: Request):
    return [
        {"panel_id": panel.stem, "count": len(json.loads(panel.read_text())["seeds"])}
        for panel in sorted((request.app.state.store.root / "panels").glob("*.json"))
    ]


@router.post("/api/panels", dependencies=[Depends(require_operator)])
def make_panel(request: Request, data: PanelInput):
    panel_id = uuid.uuid4().hex
    path = request.app.state.store.root / "panels" / f"{panel_id}.json"
    return {"panel_id": panel_id, **seed_panel(path, data.count)}


@router.post("/api/batches", dependencies=[Depends(require_operator)])
def make_batch(request: Request, data: BatchInput):
    store = request.app.state.store
    panel = json.loads((store.root / "panels" / f"{identifier(data.panel_id)}.json").read_text())
    return plan_batch(store, request.app.state.config, panel, data.agents, data.replicates)


@router.get("/api/batches", dependencies=[Depends(require_operator)])
def batches(request: Request):
    return [
        json.loads(path.read_text())
        for path in sorted((request.app.state.store.root / "batches").glob("*/plan.json"))
    ]


@router.post("/api/batches/{batch_id}/run", dependencies=[Depends(require_operator)])
def batch_run(request: Request, batch_id: str, data: BatchRun):
    state = request.app.state
    identifier(batch_id)
    with state.runs._guard:
        if state.runs.thread and state.runs.thread.is_alive():
            raise ValueError("WORKER_BUSY")
        state.runs.stop.clear()
        state.runs._launch(lambda: state.runs.run_batch(state.config, batch_id, offline=data.offline))
    return {"batch_id": batch_id, "queued": True}


@router.get("/api/batches/{batch_id}/report", dependencies=[Depends(require_operator)])
def report(request: Request, batch_id: str):
    state = request.app.state
    for row in state.store.list_episodes():
        if row["manifest"].get("batch_id") == batch_id:
            state.review.expose(row["episode_id"], "batch_report", outcome_seen=True, model_identity_seen=True)
    return report_batch(state.store, identifier(batch_id), state.output_root / "reports/generated" / batch_id)


@router.post("/api/batches/{batch_id}/export", dependencies=[Depends(require_operator)])
def export(request: Request, batch_id: str):
    export_id = uuid.uuid4().hex
    output = request.app.state.output_root / "exports" / batch_id / export_id
    result = export_batch(request.app.state.store, identifier(batch_id), output)
    return {**result, "download": f"/exports/{batch_id}/{export_id}"}


@router.get("/api/exports/{batch_id}/{export_id}", dependencies=[Depends(require_operator)])
def download_export(request: Request, batch_id: str, export_id: str):
    path = request.app.state.output_root / "exports" / identifier(batch_id) / identifier(export_id) / "public.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return FileResponse(path, media_type="application/json", filename=f"balatro-horizons-{batch_id}.json")
