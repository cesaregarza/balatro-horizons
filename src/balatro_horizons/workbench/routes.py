"""Routes that can reveal or mutate review/workbench state.

The router is included only when ``Config.workbench_enabled`` is true. This
keeps the ordinary dashboard API deliberately read-only and makes the flag
boundary observable as a 404 rather than an authorization error.
"""

import queue

from fastapi import APIRouter, Depends, Request

from balatro_horizons.api.middleware import require_operator, require_session
from balatro_horizons.api.models import (
    BranchInput,
    OpenReview,
    SeekReview,
    VerifyInput,
)
from balatro_horizons.evidence.certification import (
    require_checkpoint_certificate,
    verify_checkpoint,
)
from balatro_horizons.harness.context.freeze import restore_protocol
from balatro_horizons.harness.skills import restore_knowledge
from balatro_horizons.review.export import export_response

router = APIRouter()


@router.post("/api/reviews", dependencies=[Depends(require_operator)])
def open_review(request: Request, data: OpenReview):
    return request.app.state.workbench.open(
        data.episode_id,
        retrospective=data.retrospective,
        prior_seed_exposure=data.prior_seed_exposure,
    )


@router.get("/api/review")
def view(request: Request, token=Depends(require_session)):
    return request.app.state.workbench.view(token)


@router.post("/api/review/advance")
def advance(request: Request, token=Depends(require_session)):
    return request.app.state.workbench.advance(token)


@router.get("/api/review/decisions")
def decisions(request: Request, token=Depends(require_session)):
    return request.app.state.workbench.decisions(token)


@router.get("/api/review/decisions/{decision}")
def decision_detail(request: Request, decision: int, token=Depends(require_session)):
    return request.app.state.workbench.decision(token, decision)


@router.get("/api/review/decisions/{decision}/trace")
def dev_trace(request: Request, decision: int, token=Depends(require_session)):
    from balatro_horizons.review.dev_trace import decision_trace

    return decision_trace(request.app.state.workbench, token, decision)


@router.get("/api/review/export/{format}")
def export_decisions(request: Request, format: str, token=Depends(require_session)):
    if format not in ("json", "jsonl"):
        raise ValueError("UNKNOWN_EXPORT_FORMAT")
    ledger = request.app.state.workbench.decisions(token)
    return export_response(ledger, format)


@router.post("/api/review/seek")
def seek(request: Request, data: SeekReview, token=Depends(require_session)):
    return request.app.state.workbench.seek(token, data.decision)


@router.get("/api/review/branch-capability")
def branch_capability(request: Request, token=Depends(require_session)):
    review = request.app.state.workbench
    view = review.view(token)
    try:
        checkpoint, _ = require_checkpoint_certificate(
            request.app.state.store, view["episode_id"], view["decision"]
        )
        restore_knowledge(request.app.state.store, checkpoint)
        restore_protocol(request.app.state.store, checkpoint)
        return {"enabled": True, "reason": None}
    except (ValueError, OSError):
        return {
            "enabled": False,
            "reason": "This decision needs a current passing replay certificate and frozen "
            "knowledge and agent-protocol snapshots.",
        }


@router.post("/api/verify", dependencies=[Depends(require_operator)])
def verify(request: Request, data: VerifyInput):
    state = request.app.state
    with state.runs._guard:
        if state.runs.thread and state.runs.thread.is_alive():
            raise ValueError("WORKER_BUSY")
        parent = state.store.manifest(data.episode_id, True)
        return verify_checkpoint(
            state.store,
            state.config.model_validate(parent.get("config", state.config.model_dump())),
            data.episode_id,
            data.decision,
            mode=data.mode,
        )


@router.post("/api/branches", dependencies=[Depends(require_operator)])
def branch(request: Request, data: BranchInput):
    state = request.app.state
    parent = state.store.manifest(data.episode_id, True)
    config = state.config.model_validate(parent.get("config", state.config.model_dump()))
    return {
        "episode_id": state.runs.branch(
            config, data.episode_id, data.decision, data.mode, data.actions
        )
    }


@router.get(
    "/api/operator/branches/{branch_id}/comparison",
    dependencies=[Depends(require_operator)],
)
def compare_branch(request: Request, branch_id: str):
    state = request.app.state
    child = state.store.manifest(branch_id)
    parent_id = child.get("parent_episode_id")
    if not parent_id:
        raise ValueError("EPISODE_IS_NOT_A_BRANCH")
    result = {
        "assistance": child["assistance"],
        "branch_decision": child["parent_decision"],
        "interpretation": (
            "A successful alternative continuation does not establish an optimal move or a "
            "causal share of failure."
        ),
        "runs": [],
    }
    for episode_id in (parent_id, branch_id):
        result["runs"].append(_comparison_run(state, episode_id))
    return result


def _comparison_run(state, episode_id):
    events = state.store.events(episode_id)
    state.review.expose(
        episode_id,
        "branch_comparison",
        outcome_seen=True,
        model_identity_seen=True,
        max_event_seen=len(events) - 1,
    )
    return {
        "episode_id": episode_id,
        "summary": state.store.summary(episode_id),
        "trajectory": [
            {
                "decision": event["observation_id"],
                "phase": event["payload"]["phase"],
                "resources": event["payload"]["state"]["resources"],
                "progress": event["payload"]["state"]["progress"],
                "build": [card["label"] for card in event["payload"]["state"]["jokers"]],
            }
            for event in events
            if event["type"] == "observation"
        ],
    }


@router.get("/api/review/annotations")
def legacy_annotations(request: Request, token=Depends(require_session)):
    review = request.app.state.workbench
    view = review.view(token)
    return review.annotations_for_view(view)


@router.post("/api/review/annotations")
def legacy_annotate(request: Request, data: dict, token=Depends(require_session)):
    from balatro_horizons.contracts import AnnotationInput

    return request.app.state.workbench.annotate(token, AnnotationInput.model_validate(data))


@router.get("/api/operator/human", dependencies=[Depends(require_operator)])
def human(request: Request):
    human_policy = request.app.state.runs.human
    if not human_policy or not human_policy.current:
        return {"waiting": False}
    if request.app.state.runs.active_id:
        request.app.state.review.expose(
            request.app.state.runs.active_id,
            "human_control",
            model_identity_seen=True,
            max_event_seen=(
                len(request.app.state.store.events(request.app.state.runs.active_id)) - 1
            ),
        )
    return {"waiting": True, **human_policy.current}


@router.post("/api/operator/human", dependencies=[Depends(require_operator)])
def human_action(request: Request, data: dict):
    human_policy = request.app.state.runs.human
    if not human_policy or not human_policy.current:
        raise ValueError("NO_HUMAN_CONTROLLER")
    try:
        human_policy.queue.put_nowait(data)
    except queue.Full:
        raise ValueError("ACTION_ALREADY_QUEUED") from None
    return {"queued": True}
