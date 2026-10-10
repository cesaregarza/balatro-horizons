"""Count the complete provider input before generation; never infer tokens from ciphertext."""

import json
import os

import httpx

from balatro_horizons.config import INPUT_TOKEN_SAFETY_MARGIN
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.storage.journal import digest


def request_size(body):
    # Matches httpx's JSON encoding, including multibyte text.
    return len(
        json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )


def check_request_bytes(body, limits):
    size = request_size(body)
    if size > limits.max_request_bytes:
        raise HarnessFailure(
            "LOCAL_CONTEXT_LIMIT",
            stage="request",
            request_bytes=size,
            byte_limit=limits.max_request_bytes,
        )
    return size


def count_payload(body, provider):
    from balatro_horizons.harness.transport import runtime_type

    return runtime_type(provider).count_payload(body)


class InputCounter:
    def __init__(self):
        self._key = self._tokens = None

    def check(self, policy, body):
        size = check_request_bytes(body, policy.limits)
        payload = policy.count_payload(body)
        key = digest(payload)
        if key != self._key:
            credential = os.environ.get(policy.key_name)
            if not credential:
                raise HarnessFailure("TOKEN_COUNT_UNAVAILABLE", stage="input_token_count")
            response = _count_response(policy, credential, payload)
            try:
                result = response.json()
                tokens = result.get("input_tokens") if isinstance(result, dict) else None
            except ValueError:
                tokens = None
            if type(tokens) is not int or tokens < 0:
                raise HarnessFailure("TOKEN_COUNT_UNAVAILABLE", stage="input_token_count")
            self._key, self._tokens = key, tokens
        tokens = self._tokens
        if tokens + INPUT_TOKEN_SAFETY_MARGIN > policy.limits.max_input_tokens_per_call:
            raise HarnessFailure(
                "INPUT_TOKEN_LIMIT",
                stage="input_token_count",
                input_tokens=tokens,
                token_margin=INPUT_TOKEN_SAFETY_MARGIN,
                token_limit=policy.limits.max_input_tokens_per_call,
            )
        return {
            "method": "provider_count_v1",
            "count_payload_hash": key,
            "input_tokens": tokens,
            "token_margin": INPUT_TOKEN_SAFETY_MARGIN,
            "token_limit": policy.limits.max_input_tokens_per_call,
            "request_bytes": size,
            "byte_limit": policy.limits.max_request_bytes,
        }


def _count_response(policy, credential, payload):
    from balatro_horizons.harness.transport.errors import (
        InputCountFailure,
        ProviderFailure,
        check_status,
    )

    policy._check_stop()
    try:
        response = policy.client.post(
            policy.count_endpoint, headers=policy.headers(credential), json=payload
        )
    except httpx.TransportError:
        # Counting is free and has no generation side effect, including a lost response.
        raise InputCountFailure(retryable=True) from None
    policy._check_stop()
    try:
        check_status(response, policy.model.provider)
    except ProviderFailure as error:
        raise InputCountFailure(
            retryable=error.retryable, retry_after=error.retry_after,
            http_status=response.status_code,
        ) from None
    if response.status_code != 200:
        raise InputCountFailure(http_status=response.status_code)
    return response
