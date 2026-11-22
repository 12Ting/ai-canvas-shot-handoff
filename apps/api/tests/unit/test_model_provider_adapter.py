import json

import pytest
from pydantic import SecretStr

from ai_canvas.infrastructure.model_providers.base import (
    ProviderHttpResponse,
    ProviderTransport,
)
from ai_canvas.infrastructure.model_providers.openai_compatible import (
    ModelProviderError,
    OpenAICompatibleChatJsonAdapter,
)


class FakeTransport(ProviderTransport):
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.response


def _response(content: dict, *, status: int = 200) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=status,
        body=json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        ).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def test_adapter_injects_secret_only_at_send_time_and_uses_fixed_request() -> None:
    secret = "C3-CANARY-DO-NOT-PERSIST"
    transport = FakeTransport(
        _response(
            {
                "kind": "run_request_draft",
                "schema_version": 1,
                "operation_kind": "DRY_RUN",
                "target_node_ids": ["11111111-1111-4111-8111-111111111111"],
                "task_count": 1,
                "reason": "witness",
            }
        )
    )
    adapter = OpenAICompatibleChatJsonAdapter(transport=transport)
    payload = b'{"model":"loopback","messages":[],"stream":false}'

    result = adapter.send(
        endpoint_url="http://127.0.0.1:8765/v1/chat/completions",
        canonical_payload=payload,
        credential=SecretStr(secret),
        timeout_seconds=12,
        max_response_bytes=100_000,
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:8765/v1/chat/completions"
    assert request.body == payload
    assert request.timeout_seconds == 12
    assert request.allow_redirects is False
    assert request.headers == {
        "Accept": "application/json",
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    assert secret not in repr(result)
    assert len(result.response_sha256) == 64
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }


def test_adapter_none_credential_omits_authorization_and_never_retries() -> None:
    transport = FakeTransport(_response({"kind": "invalid"}, status=503))
    adapter = OpenAICompatibleChatJsonAdapter(transport=transport)
    with pytest.raises(ModelProviderError) as captured:
        adapter.send(
            endpoint_url="http://127.0.0.1:8765/v1/chat/completions",
            canonical_payload=b"{}",
            credential=None,
            timeout_seconds=1,
            max_response_bytes=1000,
        )
    assert len(transport.requests) == 1
    assert "Authorization" not in transport.requests[0].headers
    assert captured.value.code == "PROVIDER_HTTP_ERROR"
    assert "503" not in str(captured.value)
    assert "invalid" not in str(captured.value)


def test_adapter_fails_closed_on_oversize_or_malformed_wrapper() -> None:
    oversize = FakeTransport(
        ProviderHttpResponse(status_code=200, body=b"x" * 101, headers={})
    )
    with pytest.raises(ModelProviderError, match="response limit"):
        OpenAICompatibleChatJsonAdapter(transport=oversize).send(
            endpoint_url="http://127.0.0.1:8765/v1/chat/completions",
            canonical_payload=b"{}",
            credential=None,
            timeout_seconds=1,
            max_response_bytes=100,
        )

    malformed = FakeTransport(
        ProviderHttpResponse(status_code=200, body=b'{"choices":[]}', headers={})
    )
    with pytest.raises(ModelProviderError, match="response schema"):
        OpenAICompatibleChatJsonAdapter(transport=malformed).send(
            endpoint_url="http://127.0.0.1:8765/v1/chat/completions",
            canonical_payload=b"{}",
            credential=None,
            timeout_seconds=1,
            max_response_bytes=100,
        )
