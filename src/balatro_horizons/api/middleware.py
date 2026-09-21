"""HTTP boundary policy shared by the dashboard route groups."""

import secrets
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse


def validate_public_origin(public_origin, allowed_hosts):
    if not public_origin:
        return None, None
    external = urlsplit(public_origin)
    # The remote exception is deliberately limited to authenticated Tailscale
    # HTTPS names; accepting arbitrary origins would expose the bootstrap token.
    if (
        external.scheme != "https"
        or not external.hostname
        or not external.hostname.endswith(".ts.net")
        # User information is never part of the authority we compare below.
        or external.username is not None
        or external.password is not None
        # A public origin names one authority, not an alternate application path.
        or external.path not in ("", "/")
        or external.query
        or external.fragment
        # urlsplit accepts an explicit zero port, but it cannot be served.
        or external.port == 0
    ):
        raise ValueError("INVALID_TAILNET_ORIGIN")
    authority = external.netloc.lower()
    allowed_hosts.append(external.hostname)
    return external.hostname, authority


def install_middleware(app, allowed_hosts, public_origin, public_host, public_authority):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.middleware("http")(origin_guard(public_origin, public_host, public_authority))
    app.add_exception_handler(ValueError, safe_value_error)
    app.add_exception_handler(FileNotFoundError, missing)


def origin_guard(public_origin, public_host, public_authority):
    async def guard(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        wrong_authority = False
        if public_host and request.url.hostname == public_host:
            expected = public_origin
            # TrustedHost validates the host name; this also pins the advertised
            # port so a sibling service cannot reuse the browser's credentials.
            wrong_authority = request.headers.get("host", "").lower() != public_authority
        # Same-origin requests may omit Origin. When supplied, require an exact
        # match so hostile pages cannot drive the local operator API.
        if wrong_authority or (origin and origin != expected):
            return JSONResponse({"error": "ORIGIN_FORBIDDEN"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    return guard


async def safe_value_error(request, error):
    code = str(error)
    return JSONResponse(
        {"error": code if code.isupper() and len(code) < 100 else "INVALID_REQUEST"},
        status_code=400,
    )


async def missing(request, error):
    return JSONResponse({"error": "NOT_FOUND"}, status_code=404)


def require_operator(request: Request, x_bh_operator: str = Header(default="")):
    if not secrets.compare_digest(x_bh_operator, request.app.state.operator_token):
        raise HTTPException(403, "OPERATOR_TOKEN_REQUIRED")


def require_session(request: Request, x_review_token: str = Header(default="")):
    try:
        request.app.state.review.session(x_review_token)
    except (ValueError, FileNotFoundError):
        raise HTTPException(403, "REVIEW_TOKEN_REQUIRED") from None
    return x_review_token


def ensure_loopback(host, allow_remote):
    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        raise ValueError("REMOTE_BIND_REQUIRES_ALLOW_REMOTE")
