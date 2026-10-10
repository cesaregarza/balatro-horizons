"""Workspace routing is backend authentication, never model-facing configuration."""

import json

import httpx
import pytest
from provider_transport import stream_response
from test_claude_support import claude, context
from test_provider_continuations import model

from balatro_horizons.config import Config, Limits
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.transport import DirectProvider


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("workspace", [None, "", "wrkspc_mock_private"])
def test_count_and_generation_share_provider_headers_without_body_leaks(
    monkeypatch, provider, workspace,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-only")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-only")
    if workspace is None:
        monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", workspace)
    selected = claude("Haiku 5.5", reasoning_effort="xhigh") if provider == "anthropic" else model(provider)
    requests = []

    def receive(request):
        requests.append(request)
        if request.url.path.endswith(("/count_tokens", "/input_tokens")):
            return httpx.Response(200, json={"input_tokens": 42})
        return stream_response({"status": "completed", "content": [], "output": []}, provider)

    policy = DirectProvider(selected, Limits(), httpx.Client(transport=httpx.MockTransport(receive)))
    body = policy.request(context(), [])
    measurement = policy.check_input(body)
    policy.send(body)
    assert [request.url.path for request in requests] == (
        ["/v1/messages/count_tokens", "/v1/messages"] if provider == "anthropic"
        else ["/v1/responses/input_tokens", "/v1/responses"]
    )
    for request in requests:
        if provider == "anthropic":
            assert request.headers["x-api-key"] == "anthropic-test-only"
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert request.headers.get("anthropic-workspace-id") == (workspace or None)
        else:
            assert request.headers["authorization"] == "Bearer openai-test-only"
            assert "anthropic-workspace-id" not in request.headers
            assert "x-api-key" not in request.headers
        assert b"wrkspc_mock_private" not in request.content
    assert "wrkspc_mock_private" not in json.dumps(measurement)
    assert "wrkspc_mock_private" not in json.dumps(Config(models={"chosen": selected}).public())


def test_workspace_refusal_still_prevents_generation_and_sanitizes_diagnostics(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-only")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_mock_private")
    paths = []

    def receive(request):
        paths.append(request.url.path)
        assert request.headers["anthropic-workspace-id"] == "wrkspc_mock_private"
        return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                                                 "message": "wrkspc_mock_private rejected"}})

    policy = DirectProvider(claude("Haiku 5.5"), Limits(),
                            httpx.Client(transport=httpx.MockTransport(receive)))
    with pytest.raises(HarnessFailure, match="TOKEN_COUNT_UNAVAILABLE") as failure:
        policy.send(policy.request(context(), []))
    assert paths == ["/v1/messages/count_tokens"]
    assert failure.value.public() == {"code": "TOKEN_COUNT_UNAVAILABLE",
                                     "stage": "input_token_count", "http_status": 400}
