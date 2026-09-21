"""Append-only reviewer annotation endpoints."""

from fastapi import APIRouter, Depends, Query, Request

from balatro_horizons.contracts import AnnotationInput

from .middleware import require_session

router = APIRouter()


@router.get("/api/explore/annotations")
def annotations(
    request: Request,
    decision: int | None = Query(default=None),
    token=Depends(require_session),
):
    review = request.app.state.review
    view = review.explore_view(token) if decision is None else review.decision(token, decision)
    return review.annotations_for_view(view)


@router.post("/api/explore/annotations")
def annotate(request: Request, data: AnnotationInput, token=Depends(require_session)):
    return request.app.state.review.annotate(token, data)
