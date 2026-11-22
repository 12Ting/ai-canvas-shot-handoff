from dataclasses import dataclass
from hashlib import sha256
import json
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import SecretStr

from ai_canvas.domain.control.canonical import canonical_json
from ai_canvas.infrastructure.model_providers.base import (
    ProviderHttpRequest,
    ProviderHttpResponse,
    ProviderTransport,
)


class ModelProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelProviderResult:
    output_json: bytes
    response_sha256: str
    usage: dict[str, int]
    latency_ms: int


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


class UrllibProviderTransport:
    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        if request.allow_redirects:
            raise ModelProviderError("PROVIDER_POLICY_ERROR", "provider redirects are forbidden")
        opener = build_opener(_NoRedirectHandler())
        url_request = Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with opener.open(url_request, timeout=request.timeout_seconds) as response:
                body = response.read(request.max_response_bytes + 1)
                return ProviderHttpResponse(
                    status_code=response.status,
                    body=body,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as error:
            return ProviderHttpResponse(
                status_code=error.code,
                body=b"",
                headers={},
            )
        except (URLError, TimeoutError, OSError) as error:
            raise ModelProviderError(
                "PROVIDER_TRANSPORT_ERROR", "model provider transport failed"
            ) from error


class OpenAICompatibleChatJsonAdapter:
    def __init__(self, *, transport: ProviderTransport | None = None) -> None:
        self.transport = transport or UrllibProviderTransport()

    def send(
        self,
        *,
        endpoint_url: str,
        canonical_payload: bytes,
        credential: SecretStr | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> ModelProviderResult:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if credential is not None:
            headers["Authorization"] = f"Bearer {credential.get_secret_value()}"
        request = ProviderHttpRequest(
            method="POST",
            url=endpoint_url,
            headers=headers,
            body=canonical_payload,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            allow_redirects=False,
        )
        started = monotonic()
        response = self.transport.send(request)
        latency_ms = max(0, round((monotonic() - started) * 1000))
        if not 200 <= response.status_code < 300:
            raise ModelProviderError(
                "PROVIDER_HTTP_ERROR", "model provider returned a non-success response"
            )
        if len(response.body) > max_response_bytes:
            raise ModelProviderError(
                "PROVIDER_RESPONSE_TOO_LARGE", "model provider exceeded the response limit"
            )
        try:
            wrapper = json.loads(response.body)
            choices = wrapper["choices"]
            content = choices[0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            parsed_output = json.loads(content)
            if not isinstance(parsed_output, dict):
                raise TypeError
            usage_value = wrapper.get("usage", {})
            if not isinstance(usage_value, dict):
                raise TypeError
            usage: dict[str, int] = {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value: Any = usage_value.get(key)
                if value is not None:
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise TypeError
                    usage[key] = value
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ModelProviderError(
                "PROVIDER_RESPONSE_SCHEMA_ERROR", "model provider response schema is invalid"
            ) from error
        output_json = canonical_json(parsed_output).encode("utf-8")
        return ModelProviderResult(
            output_json=output_json,
            response_sha256=sha256(response.body).hexdigest(),
            usage=usage,
            latency_ms=latency_ms,
        )
