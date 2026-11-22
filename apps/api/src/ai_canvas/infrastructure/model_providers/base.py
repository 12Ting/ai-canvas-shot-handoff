from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderHttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_seconds: int
    max_response_bytes: int
    allow_redirects: bool = False


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]


class ProviderTransport(Protocol):
    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse: ...
