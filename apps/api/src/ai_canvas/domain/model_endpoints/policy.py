from collections.abc import Callable, Iterable
from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import urlsplit

from ai_canvas.domain.control.canonical import canonical_sha256


Resolver = Callable[[str], Iterable[str]]
_REMOTE_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class EndpointPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class EndpointPolicy:
    canonical_url: str
    locality: str
    resolved_addresses: tuple[str, ...]
    identity_sha256: str
    allow_redirects: bool = False


def _addresses(
    values: Iterable[str],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        unique = {ipaddress.ip_address(value) for value in values}
    except ValueError as error:
        raise EndpointPolicyError(
            "endpoint resolution returned an invalid IP address"
        ) from error
    if not unique:
        raise EndpointPolicyError("endpoint resolution returned no addresses")
    return tuple(sorted(unique, key=lambda item: (item.version, int(item))))


def _canonical_url(scheme: str, host: str, port: int | None) -> str:
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = f":{port}" if port is not None else ""
    return f"{scheme}://{rendered_host}{rendered_port}/v1/chat/completions"


def validate_endpoint(
    endpoint_url: str,
    *,
    locality: str,
    resolver: Resolver | None = None,
) -> EndpointPolicy:
    try:
        parsed = urlsplit(endpoint_url)
        port = parsed.port
    except ValueError as error:
        raise EndpointPolicyError("endpoint URL is malformed") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1/chat/completions"
    ):
        raise EndpointPolicyError("endpoint URL has a forbidden component")
    host = parsed.hostname.lower()

    if locality == "LOCAL_LOOPBACK":
        if parsed.scheme != "http" or port is None:
            raise EndpointPolicyError("loopback endpoints require explicit HTTP port")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise EndpointPolicyError("loopback endpoints require an IP literal") from error
        if not address.is_loopback or str(address) not in {"127.0.0.1", "::1"}:
            raise EndpointPolicyError("loopback endpoint is not an exact loopback address")
        resolved = (address,)
    elif locality == "REMOTE_HTTPS":
        if parsed.scheme != "https":
            raise EndpointPolicyError("remote endpoints require HTTPS")
        if host == "localhost":
            raise EndpointPolicyError("ambiguous localhost names are forbidden")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            if not _REMOTE_HOST.fullmatch(host):
                raise EndpointPolicyError("remote endpoint hostname is not canonical")
            if resolver is None:
                raise EndpointPolicyError("remote endpoint DNS resolution is required")
            resolved = _addresses(resolver(host))
        else:
            resolved = (literal,)
        if any(not address.is_global for address in resolved):
            raise EndpointPolicyError("remote endpoint resolved to a non-public address")
    else:
        raise EndpointPolicyError("unknown endpoint locality")

    canonical = _canonical_url(parsed.scheme, host, port)
    if canonical != endpoint_url:
        raise EndpointPolicyError("endpoint URL is not in canonical form")
    rendered_addresses = tuple(str(address) for address in resolved)
    identity = canonical_sha256(
        {
            "canonical_url": canonical,
            "locality": locality,
            "resolved_addresses": list(rendered_addresses),
            "redirects": "DISABLED",
        }
    )
    return EndpointPolicy(
        canonical_url=canonical,
        locality=locality,
        resolved_addresses=rendered_addresses,
        identity_sha256=identity,
    )
