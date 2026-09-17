"""Count the complete provider input before generation; never infer tokens from ciphertext."""

import json
import os
from copy import deepcopy

import httpx

from balatro_horizons.agents.failures import HarnessFailure
from balatro_horizons.config import INPUT_TOKEN_SAFETY_MARGIN
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
    # Only counting-endpoint fields; input items/tools are passed unchanged.
    fields = (
        (
            "model",
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "text",
            "truncation",
            "previous_response_id",
            "conversation",
        )
        if provider == "openai"
        else ("model", "messages", "system", "tools", "tool_choice", "thinking")
    )
    return deepcopy({k: body[k] for k in fields if k in body})


class InputCounter:
    def __init__(self):
        self._key = self._tokens = None

    def check(self, policy, body):
        size = check_request_bytes(body, policy.limits)
        payload = count_payload(body, policy.model.provider)
        key = digest(payload)
        if key != self._key:
            credential = os.environ.get(policy.key_name)
            if not credential:
                raise HarnessFailure("TOKEN_COUNT_UNAVAILABLE", stage="input_token_count")
            if policy.model.provider == "openai":
                url = "https://api.openai.com/v1/responses/input_tokens"
                headers = {"Authorization": "Bearer " + credential}
            else:
                url = "https://api.anthropic.com/v1/messages/count_tokens"
                headers = {"x-api-key": credential, "anthropic-version": "2023-06-01"}
            try:
                response = policy.client.post(url, headers=headers, json=payload)
            except httpx.TransportError:
                raise HarnessFailure("TOKEN_COUNT_UNAVAILABLE", stage="input_token_count") from None
            if response.status_code != 200:
                raise HarnessFailure(
                    "TOKEN_COUNT_UNAVAILABLE",
                    stage="input_token_count",
                    http_status=response.status_code,
                )
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
