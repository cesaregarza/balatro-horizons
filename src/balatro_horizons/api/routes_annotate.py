"""Append-only reviewer annotation endpoints."""

from fastapi import APIRouter, Depends, Request

from balatro_horizons.contracts import AnnotationInput

from .middleware import require_session

router = APIRouter()


@router.get("/api/explore/annotations")
def annotations(request: Request, token=Depends(require_session)):
    review = request.app.state.review
    view = review.explore_view(token)
    return review.annotations(view["episode_id"])


@router.post("/api/explore/annotations")
def annotate(request: Request, data: AnnotationInput, token=Depends(require_session)):
    return request.app.state.review.annotate(token, data)
