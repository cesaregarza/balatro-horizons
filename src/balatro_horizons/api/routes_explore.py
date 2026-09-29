"""Read-only run-library and retrospective explorer projections."""

from fastapi import APIRouter, Depends, Request

from balatro_horizons.api.models import OpenExplorer, SeekReview
from balatro_horizons.review.export import export_response

from .middleware import require_operator, require_session

router = APIRouter()


@router.get("/api/episodes", dependencies=[Depends(require_operator)])
def episodes(request: Request):
    rows = []
    for row in request.app.state.store.list_episodes():
        manifest = row["manifest"]
        config = manifest.get("config", {})
        rows.append(
            {
                "episode_id": row["episode_id"],
                "created_at": manifest["created_at"],
                "evidence_kind": manifest["evidence_kind"],
                "evaluation_eligible": manifest.get("evaluation_eligible", False),
                "fixture": manifest.get("fixture"),
                "deck": config.get("deck"),
                "stake": config.get("stake"),
                "agent": manifest.get("agent", "unknown"),
                "branch": bool(manifest.get("parent_episode_id")),
            }
        )
    return rows


@router.get("/api/operator/episodes", dependencies=[Depends(require_operator)])
def operator_episodes(request: Request):
    return request.app.state.operator_library.episodes()


@router.post("/api/explore/sessions", dependencies=[Depends(require_operator)])
def open_explorer(request: Request, data: OpenExplorer):
    if not data.retrospective:
        raise ValueError("RETROSPECTIVE_REVIEW_REQUIRED")
    return request.app.state.review.open_explorer(
        data.episode_id,
        prior_seed_exposure=data.prior_seed_exposure,
        include_view=data.include_view,
    )


@router.get("/api/explore/decisions")
def decisions(request: Request, token=Depends(require_session)):
    return request.app.state.review.decisions(token)


@router.get("/api/explore/decisions/{decision}")
def decision_detail(request: Request, decision: int, technical: bool = True,
                    token=Depends(require_session)):
    return request.app.state.review.decision(token, decision, technical=technical)


@router.get("/api/explore/decisions/{decision}/trace")
def dev_trace(request: Request, decision: int, token=Depends(require_session)):
    from balatro_horizons.review.dev_trace import decision_trace

    return decision_trace(request.app.state.review, token, decision)


@router.get("/api/explore/export/{format}")
def export_decisions(request: Request, format: str, token=Depends(require_session)):
    if format not in ("json", "jsonl"):
        raise ValueError("UNKNOWN_EXPORT_FORMAT")
    return export_response(request.app.state.review.decisions(token), format)


@router.post("/api/explore/seek")
def seek(request: Request, data: SeekReview, token=Depends(require_session)):
    return request.app.state.review.seek(token, data.decision)
