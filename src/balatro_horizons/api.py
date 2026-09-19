"""Local operator controls and separately scoped prospective review routes."""

import json
import secrets
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from balatro_horizons.agents.frozen import restore_protocol
from balatro_horizons.agents.skills import restore_knowledge
from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.engine.certification import require_checkpoint_certificate, verify_checkpoint
from balatro_horizons.engine.windows_context import connection_status, load_session
from balatro_horizons.evaluation.batches import plan_batch, seed_panel
from balatro_horizons.evaluation.reports import export_batch, report_batch
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json, identifier


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(Input):
    agent: str = "heuristic"
    offline: bool = True
    calibration: bool = False
    seed: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    preset: Literal["pilot", "smoke"] = "pilot"


class OpenReview(Input):
    episode_id: str
    retrospective: bool = False
    prior_seed_exposure: bool = False


class SeekReview(Input):
    decision: int = Field(ge=0, strict=True)


class BranchInput(Input):
    episode_id: str
    decision: int = Field(ge=0)
    mode: Literal[
        "agent_continue", "single_action_override", "short_human_sequence", "human_takeover"
    ]
    actions: list[dict] = Field(default_factory=list, max_length=20)


class PanelInput(Input):
    count: int = Field(default=20, ge=1, le=200)


class BatchInput(Input):
    panel_id: str
    agents: list[str] = Field(min_length=1, max_length=10)
    replicates: int = Field(default=2, ge=1, le=10)


class BatchRun(Input):
    offline: bool = True


class VerifyInput(Input):
    mode: Literal["checkpoint", "seed_prefix", "checkpoint_probe", "seed_prefix_probe"] = "checkpoint"
    episode_id: str
    decision: int = Field(ge=0)
    probe_action: dict | None = None


class BudgetContinuationInput(Input):
    combined_cap_usd: float = Field(gt=0, allow_inf_nan=False, strict=True)
    parent_terminal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SettingsInput(Input):
    skills: Literal["balatro-guide-v1", "none"] = "balatro-guide-v1"
    budgets: dict
    models: dict


def create_app(data_dir=None, config=None, *, public_origin=None):
    allowed_hosts = ["127.0.0.1", "localhost", "testserver"]
    public_host = public_authority = None
    if public_origin:
        external = urlsplit(public_origin)
        if (
            external.scheme != "https"
            or not external.hostname
            or not external.hostname.endswith(".ts.net")
            or external.username is not None
            or external.password is not None
            or external.path not in ("", "/")
            or external.query
            or external.fragment
            or external.port == 0
        ):
            raise ValueError("INVALID_TAILNET_ORIGIN")
        public_host = external.hostname
        public_authority = external.netloc.lower()
        public_origin = "https://" + public_authority
        allowed_hosts.append(public_host)
    store = Store(data_dir or ROOT / "data")
    review = ReviewService(store)
    operator_status = OperatorStatus(store, review)
    runs = RunService(store, review)
    cfg = config or load_config()
    settings = store.root / "operator-settings.json"
    if settings.exists():
        cfg = Config.model_validate(json.loads(settings.read_text()))
    operator_token = secrets.token_hex(32)
    app = FastAPI(title="Balatro Horizons", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.state.store, app.state.review, app.state.runs = store, review, runs

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        expected_origin = str(request.base_url).rstrip("/")
        wrong_authority = False
        if public_host and request.url.hostname == public_host:
            expected_origin = public_origin
            wrong_authority = request.headers.get("host", "").lower() != public_authority
        if wrong_authority or (origin and origin != expected_origin):
            return __import__("starlette.responses", fromlist=["JSONResponse"]).JSONResponse(
                {"error": "ORIGIN_FORBIDDEN"}, status_code=403
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def operator(x_bh_operator: str = Header(default="")):
        if not secrets.compare_digest(x_bh_operator, operator_token):
            raise HTTPException(403, "OPERATOR_TOKEN_REQUIRED")

    def session(x_review_token: str = Header(default="")):
        try:
            review.session(x_review_token)
        except (ValueError, FileNotFoundError):
            raise HTTPException(403, "REVIEW_TOKEN_REQUIRED") from None
        return x_review_token

    @app.exception_handler(ValueError)
    async def safe_value_error(request, error):
        from starlette.responses import JSONResponse

        code = str(error)
        return JSONResponse(
            {"error": code if code.isupper() and len(code) < 100 else "INVALID_REQUEST"},
            status_code=400,
        )

    @app.exception_handler(FileNotFoundError)
    async def missing(request, error):
        from starlette.responses import JSONResponse

        return JSONResponse({"error": "NOT_FOUND"}, status_code=404)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "operator_token": operator_token,
            "config": cfg.public(),
            "paid_credentials": {
                name: bool(__import__("os").environ.get(key))
                for name, key in [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")]
            },
        }

    @app.put("/api/settings", dependencies=[Depends(operator)])
    def update_settings(data: SettingsInput):
        nonlocal cfg
        if runs.thread and runs.thread.is_alive():
            raise ValueError("WORKER_BUSY")
        cfg = Config.model_validate({**cfg.model_dump(), **data.model_dump(exclude_unset=True)})
        atomic_json(settings, cfg.model_dump())
        return cfg.public()

    @app.get("/api/episodes", dependencies=[Depends(operator)])
    def episodes():
        return [
            {
                "episode_id": row["episode_id"],
                "created_at": row["manifest"]["created_at"],
                "evidence_kind": row["manifest"]["evidence_kind"],
                "evaluation_eligible": row["manifest"].get("evaluation_eligible", False),
                "fixture": row["manifest"].get("fixture"),
                "deck": row["manifest"]["config"]["deck"],
                "stake": row["manifest"]["config"]["stake"],
                "branch": bool(row["manifest"].get("parent_episode_id")),
            }
            for row in store.list_episodes()
        ]

    @app.post("/api/runs", dependencies=[Depends(operator)])
    def start(data: RunInput):
        chosen = cfg.model_copy(deep=True)
        if data.preset == "smoke":
            chosen.environment.stake = "WHITE"
        seed = data.seed or "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
        )
        return {
            "episode_id": runs.start(
                chosen, data.agent, seed, offline=data.offline, calibration=data.calibration
            )
        }

    @app.post("/api/stop", dependencies=[Depends(operator)])
    def stop():
        runs.stop.set()
        return {"stop_requested": True}

    @app.get("/api/operator/status", dependencies=[Depends(operator)])
    def status():
        result = operator_status.episodes()
        return {
            "running": bool(runs.thread and runs.thread.is_alive()),
            "active_episode": runs.active_id,
            "error": runs.error,
            "episodes": result,
            "runtime_connection": connection_status(),
        }

    @app.get("/api/operator/runtime", dependencies=[Depends(operator)])
    def runtime_connection():
        return connection_status()

    @app.get("/api/operator/human", dependencies=[Depends(operator)])
    def human():
        if not runs.human or not runs.human.current:
            return {"waiting": False}
        if runs.active_id:
            review.expose(
                runs.active_id,
                "human_control",
                model_identity_seen=True,
                max_event_seen=len(store.events(runs.active_id)) - 1,
            )
        return {"waiting": True, **runs.human.current}

    @app.post("/api/operator/human", dependencies=[Depends(operator)])
    def human_action(data: dict):
        if not runs.human or not runs.human.current:
            raise ValueError("NO_HUMAN_CONTROLLER")
        try:
            runs.human.queue.put_nowait(data)
        except __import__("queue").Full:
            raise ValueError("ACTION_ALREADY_QUEUED") from None
        return {"queued": True}

    @app.post("/api/reviews", dependencies=[Depends(operator)])
    def open_review(data: OpenReview):
        return review.open(
            data.episode_id,
            retrospective=data.retrospective,
            prior_seed_exposure=data.prior_seed_exposure,
        )

    @app.get("/api/review")
    def view(token=Depends(session)):
        return review.view(token)

    @app.post("/api/review/advance")
    def advance(token=Depends(session)):
        return review.advance(token)

    @app.get("/api/review/decisions")
    def decisions(token=Depends(session)):
        return review.decisions(token)

    @app.get("/api/review/decisions/{decision}")
    def decision_detail(decision: int, token=Depends(session)):
        return review.decision(token, decision)

    @app.get("/api/review/decisions/{decision}/trace")
    def dev_trace(decision: int, token=Depends(session)):
        from balatro_horizons.review.dev_trace import decision_trace

        return decision_trace(review, token, decision)

    @app.post("/api/review/seek")
    def seek(data: SeekReview, token=Depends(session)):
        return review.seek(token, data.decision)

    @app.post("/api/review/annotations")
    def annotate(data: AnnotationInput, token=Depends(session)):
        return review.annotate(token, data)

    @app.get("/api/review/annotations")
    def annotations(token=Depends(session)):
        view = review.view(token)
        return [
            a
            for a in review.annotations(view["episode_id"])
            if a["end_decision"] <= view["decision"]
        ]

    @app.get("/api/review/branch-capability")
    def branch_capability(token=Depends(session)):
        view = review.view(token)
        try:
            checkpoint, _ = require_checkpoint_certificate(
                store, view["episode_id"], view["decision"]
            )
            restore_knowledge(store, checkpoint)
            restore_protocol(store, checkpoint)
            return {"enabled": True, "reason": None}
        except (ValueError, OSError):
            return {
                "enabled": False,
                "reason": "This decision needs a current passing replay certificate and frozen knowledge and agent-protocol snapshots.",
            }

    @app.post("/api/verify", dependencies=[Depends(operator)])
    def verify(data: VerifyInput):
        # Serialize verification admission with starts and branches, including
        # the full synchronous check; a new game must not enter mid-proof.
        with runs._guard:
            if runs.thread and runs.thread.is_alive():
                raise ValueError("WORKER_BUSY")
            return verify_idle(data)

    def verify_idle(data: VerifyInput):
        parent = store.manifest(data.episode_id, True)
        if data.mode in ("checkpoint_probe", "seed_prefix_probe"):
            from balatro_horizons.engine.continuation_probe import verify_continuation_probe

            if data.probe_action is None:
                raise ValueError("PROBE_ACTION_REQUIRED")
            return verify_continuation_probe(
                store, Config.model_validate(parent["config"]), data.episode_id,
                data.decision, data.probe_action,
                restoration="seed_prefix" if data.mode == "seed_prefix_probe" else "checkpoint",
            )
        if data.probe_action is not None:
            raise ValueError("PROBE_ACTION_REQUIRES_PROBE_MODE")
        return verify_checkpoint(
            store,
            Config.model_validate(parent.get("config", cfg.model_dump())),
            data.episode_id,
            data.decision,
            mode=data.mode,
        )

    @app.post("/api/operator/episodes/{eid}/continue-budget", dependencies=[Depends(operator)])
    def continue_budget(eid: str, data: BudgetContinuationInput):
        return {"episode_id": runs.continue_budget(
            eid, data.combined_cap_usd, expected_head=data.parent_terminal_hash,
        )}

    @app.post("/api/branches", dependencies=[Depends(operator)])
    def branch(data: BranchInput):
        parent = store.manifest(data.episode_id, True)
        original = Config.model_validate(parent.get("config", cfg.model_dump()))
        return {
            "episode_id": runs.branch(
                original, data.episode_id, data.decision, data.mode, data.actions
            )
        }

    @app.get("/api/operator/branches/{branch_id}/comparison", dependencies=[Depends(operator)])
    def compare_branch(branch_id: str):
        child = store.manifest(branch_id)
        parent_id = child.get("parent_episode_id")
        if not parent_id:
            raise ValueError("EPISODE_IS_NOT_A_BRANCH")
        result = {
            "assistance": child["assistance"],
            "branch_decision": child["parent_decision"],
            "interpretation": "A successful alternative continuation does not establish an optimal move or a causal share of failure.",
            "runs": [],
        }
        for eid in (parent_id, branch_id):
            events = store.events(eid)
            review.expose(
                eid,
                "branch_comparison",
                outcome_seen=True,
                model_identity_seen=True,
                max_event_seen=len(events) - 1,
            )
            result["runs"].append(
                {
                    "episode_id": eid,
                    "summary": store.summary(eid),
                    "trajectory": [
                        {
                            "decision": e["observation_id"],
                            "phase": e["payload"]["phase"],
                            "resources": e["payload"]["state"]["resources"],
                            "progress": e["payload"]["state"]["progress"],
                            "build": [c["label"] for c in e["payload"]["state"]["jokers"]],
                        }
                        for e in events
                        if e["type"] == "observation"
                    ],
                }
            )
        return result

    @app.get("/api/panels", dependencies=[Depends(operator)])
    def panels():
        return [
            {"panel_id": p.stem, "count": len(json.loads(p.read_text())["seeds"])}
            for p in sorted((store.root / "panels").glob("*.json"))
        ]

    @app.post("/api/panels", dependencies=[Depends(operator)])
    def make_panel(data: PanelInput):
        pid = uuid.uuid4().hex
        return {"panel_id": pid, **seed_panel(store.root / "panels" / (pid + ".json"), data.count)}

    @app.post("/api/batches", dependencies=[Depends(operator)])
    def make_batch(data: BatchInput):
        panel = json.loads(
            (store.root / "panels" / (identifier(data.panel_id) + ".json")).read_text()
        )
        return plan_batch(store, cfg, panel, data.agents, data.replicates)

    @app.get("/api/batches", dependencies=[Depends(operator)])
    def batches():
        return [
            json.loads(p.read_text()) for p in sorted((store.root / "batches").glob("*/plan.json"))
        ]

    @app.post("/api/batches/{bid}/run", dependencies=[Depends(operator)])
    def batch_run(bid: str, data: BatchRun):
        identifier(bid)
        with runs._guard:
            if runs.thread and runs.thread.is_alive():
                raise ValueError("WORKER_BUSY")
            if not data.offline:
                load_session()
            runs.stop.clear()
            runs._launch(lambda: runs.run_batch(cfg, bid, offline=data.offline))
        return {"batch_id": bid, "queued": True}

    @app.get("/api/batches/{bid}/report", dependencies=[Depends(operator)])
    def report(bid: str):
        for row in store.list_episodes():
            if row["manifest"].get("batch_id") == bid:
                review.expose(
                    row["episode_id"], "batch_report", outcome_seen=True, model_identity_seen=True
                )
        return report_batch(store, identifier(bid), ROOT / "reports/generated" / bid)

    @app.post("/api/batches/{bid}/export", dependencies=[Depends(operator)])
    def export(bid: str):
        export_id = uuid.uuid4().hex
        result = export_batch(store, identifier(bid), ROOT / "exports" / bid / export_id)
        return {**result, "download": f"/exports/{bid}/{export_id}"}

    @app.get("/api/exports/{bid}/{export_id}", dependencies=[Depends(operator)])
    def download_export(bid: str, export_id: str):
        path = ROOT / "exports" / identifier(bid) / identifier(export_id) / "public.json"
        if not path.is_file():
            raise HTTPException(404, "PUBLIC_EXPORT_NOT_FOUND")
        return FileResponse(
            path, media_type="application/json", filename=f"balatro-horizons-{bid}.json"
        )

    dist = ROOT / "web/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").exists():
            raise HTTPException(503, "Build the browser application first.")
        return FileResponse(dist / "index.html")

    return app
