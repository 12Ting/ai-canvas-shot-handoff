import pytest

from ai_canvas.domain.model_endpoints.policy import EndpointPolicyError, validate_endpoint


def test_loopback_requires_exact_ip_and_fixed_chat_path() -> None:
    policy = validate_endpoint(
        "http://127.0.0.1:8765/v1/chat/completions",
        locality="LOCAL_LOOPBACK",
    )
    assert policy.canonical_url == "http://127.0.0.1:8765/v1/chat/completions"
    assert policy.resolved_addresses == ("127.0.0.1",)
    assert policy.allow_redirects is False

    ipv6 = validate_endpoint(
        "http://[::1]:8765/v1/chat/completions",
        locality="LOCAL_LOOPBACK",
    )
    assert ipv6.resolved_addresses == ("::1",)

    for value in (
        "http://localhost:8765/v1/chat/completions",
        "https://127.0.0.1:8765/v1/chat/completions",
        "http://127.0.0.1:8765/v1/chat/completions/",
        "http://127.0.0.1:8765/v1/chat/completions?q=1",
        "http://user@127.0.0.1:8765/v1/chat/completions",
    ):
        with pytest.raises(EndpointPolicyError):
            validate_endpoint(value, locality="LOCAL_LOOPBACK")


def test_remote_https_requires_only_public_resolved_addresses() -> None:
    policy = validate_endpoint(
        "https://models.example.test/v1/chat/completions",
        locality="REMOTE_HTTPS",
        resolver=lambda host: ["1.1.1.1", "8.8.8.8"],
    )
    assert policy.resolved_addresses == ("1.1.1.1", "8.8.8.8")
    assert len(policy.identity_sha256) == 64

    for addresses in (["127.0.0.1"], ["10.0.0.8"], ["169.254.1.1"], ["::1"]):
        with pytest.raises(EndpointPolicyError):
            validate_endpoint(
                "https://models.example.test/v1/chat/completions",
                locality="REMOTE_HTTPS",
                resolver=lambda host, values=addresses: values,
            )


def test_dns_identity_is_order_independent_and_changes_with_address_set() -> None:
    first = validate_endpoint(
        "https://api.example.test/v1/chat/completions",
        locality="REMOTE_HTTPS",
        resolver=lambda host: ["8.8.4.4", "8.8.8.8"],
    )
    same = validate_endpoint(
        "https://api.example.test/v1/chat/completions",
        locality="REMOTE_HTTPS",
        resolver=lambda host: ["8.8.8.8", "8.8.4.4"],
    )
    changed = validate_endpoint(
        "https://api.example.test/v1/chat/completions",
        locality="REMOTE_HTTPS",
        resolver=lambda host: ["8.8.8.8"],
    )
    assert first.identity_sha256 == same.identity_sha256
    assert first.identity_sha256 != changed.identity_sha256


def test_remote_rejects_ambiguous_or_malformed_urls() -> None:
    for value in (
        "http://api.example.test/v1/chat/completions",
        "https://localhost/v1/chat/completions",
        "https://api.example.test/v1/chat/completions#fragment",
        "https://api.example.test/v1/chat/completions?x=1",
        "https://user:pass@api.example.test/v1/chat/completions",
    ):
        with pytest.raises(EndpointPolicyError):
            validate_endpoint(
                value,
                locality="REMOTE_HTTPS",
                resolver=lambda host: ["8.8.8.8"],
            )
