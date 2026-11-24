"""The cloud runner's status, read fresh from the durable records on every request."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from balatro_horizons.cloud.status import CloudStatusError, read_status

from .middleware import require_operator

router = APIRouter()


@router.get("/api/cloud/status", dependencies=[Depends(require_operator)])
def cloud_status(request: Request):
    """The report `bh cloud status` prints. The server keeps none of it between requests."""
    try:
        return read_status(request.app.state.store.root)
    except CloudStatusError as error:
        return JSONResponse({"error": error.code}, status_code=503)
