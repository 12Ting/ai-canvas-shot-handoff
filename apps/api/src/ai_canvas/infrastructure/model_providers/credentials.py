from pydantic import SecretStr


class CredentialUnavailable(ValueError):
    pass


def resolve_runtime_credential(
    *,
    credential_kind: str,
    canvas_agent_api_key: SecretStr | None,
) -> SecretStr | None:
    if credential_kind == "NONE":
        return None
    if credential_kind == "BEARER_ENV":
        if canvas_agent_api_key is None or not canvas_agent_api_key.get_secret_value():
            raise CredentialUnavailable("canvas agent runtime credential is unavailable")
        return canvas_agent_api_key
    raise CredentialUnavailable("unsupported canvas agent credential kind")
