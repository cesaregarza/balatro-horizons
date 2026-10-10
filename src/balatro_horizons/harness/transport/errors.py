"""Sanitized native errors and bounded retry hints; the runner owns retrying."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from balatro_horizons.harness.failures import HarnessFailure

MAX_RETRY_DELAY_SECONDS = 60.0
OPENAI_BILLING_CODES = frozenset({
    "insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
    "billing_limit_reached", "organization_quota_exceeded", "project_quota_exceeded",
    "organization_budget_exceeded", "project_budget_exceeded",
    "organization_billing_limit_reached", "project_billing_limit_reached",
    "usage_limit_reached", "credit_balance_exhausted", "account_deactivated",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded", "usage_limit_exceeded",
})
OPENAI_CODES = OPENAI_BILLING_CODES | {
    "rate_limit_exceeded", "model_not_found", "invalid_api_key", "invalid_request_error",
    "unsupported_parameter", "unsupported_value", "server_error", "permission_denied",
    "slow_down", "server_is_overloaded", "service_unavailable_error",
}
CLAUDE_BILLING_CODES = frozenset({"enforced_spend_limit_reached", "credit_balance_exhausted"})
CLAUDE_CODES = CLAUDE_BILLING_CODES | {
    "invalid_request_error", "authentication_error", "billing_error", "permission_error",
    "not_found_error", "request_too_large", "rate_limit_error", "api_error",
    "overloaded_error",
}


class ProviderFailure(RuntimeError):
    def __init__(self, code, retryable=False, provider_code=None, retry_after=None):
        self.code, self.retryable = code, retryable
        self.provider_code, self.retry_after = provider_code, retry_after
        super().__init__(code)


class ProtocolFailure(ValueError):
    def __init__(self, code, **details):
        self.code, self.details = code, details
        super().__init__(code)


class InputCountFailure(HarnessFailure):
    """A free admission attempt; never reserve generation spend for its retries."""

    def __init__(self, *, retryable=False, retry_after=None, **details):
        self.retryable, self.retry_after = retryable, retry_after
        super().__init__("TOKEN_COUNT_UNAVAILABLE", stage="input_token_count", **details)


def retry_after(headers):
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            seconds = (date - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return min(MAX_RETRY_DELAY_SECONDS, max(0.0, seconds))


def native_code(body, provider):
    # Upstream message text may contain prompt/credential fragments: never retain it.
    if not isinstance(body, dict):
        return None
    error = body.get("error", body)
    if not isinstance(error, dict):
        return None
    allowed = OPENAI_CODES if provider == "openai" else CLAUDE_CODES
    details = error.get("details")
    nested = details.get("error_code") if isinstance(details, dict) else None
    for candidate in (nested, error.get("code"), error.get("type")):
        if isinstance(candidate, str) and candidate in allowed:
            return candidate
    return None


def check_status(response, provider):
    if response.status_code < 400:
        return
    try:
        code = native_code(response.json(), provider)
    except ValueError:
        code = None
    statuses = {408, 409, 429, 500, 502, 503, 504}
    permanent = OPENAI_BILLING_CODES | {
        "model_not_found", "invalid_api_key", "invalid_request_error", "unsupported_parameter",
        "unsupported_value", "permission_denied",
    }
    if provider == "anthropic":
        statuses.add(529)
        permanent = CLAUDE_BILLING_CODES | {
            "billing_error", "authentication_error", "permission_error", "invalid_request_error",
            "not_found_error", "request_too_large",
        }
    retryable = response.status_code in statuses and code not in permanent
    raise ProviderFailure(
        f"PROVIDER_HTTP_{response.status_code}", retryable, code, retry_after(response.headers)
    )
