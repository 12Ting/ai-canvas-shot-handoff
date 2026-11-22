from pathlib import Path

from fastapi.testclient import TestClient

from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _activate_profile(app, project_id):
    control = app.state.control_config_service
    active = control.get_active()
    pending = ModelProfileService(control).request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=active.id,
        provider={
            "provider_key": "loopback",
            "display_name": "Loopback",
            "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
        },
        profile_key="canvas-agent-default",
        revision={
            "role": "CANVAS_AGENT",
            "model_id": "loopback-c3",
            "model_version": "1",
            "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions",
            "locality": "LOCAL_LOOPBACK",
            "data_residency": "LOCAL_DEVICE",
            "allowed_content_categories": ["USER_INSTRUCTION"],
            "retention_policy": "NO_RETENTION_DECLARED",
            "retention_note": "",
            "tool_permissions": ["RUN_REQUEST_DRAFT"],
            "cost_mode": "HARD",
            "currency": "CNY",
            "cost_bound_minor": 0,
            "max_output_tokens": 512,
            "timeout_seconds": 5,
            "credential_kind": "NONE",
            "fallback_model_profile_revision_id": None,
        },
        actor_id="local-control-admin",
    )
    control.approve(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )
    control.apply(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )


def test_model_profile_routes_are_authenticated_and_never_return_credentials(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    with TestClient(app, base_url="http://testserver") as client:
        project = app.state.project_service.create("C3 profile route")
        _activate_profile(app, project.id)
        denied = client.get("/api/v1/control/model-profiles")
        listed = client.get(
            "/api/v1/control/model-profiles",
            headers={"Authorization": f"Bearer {token}"},
        )
        profile = listed.json()["profiles"][0]
        revisions = client.get(
            f"/api/v1/control/model-profiles/{profile['id']}/revisions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert denied.status_code == 401
    assert listed.status_code == 200
    assert revisions.status_code == 200
    body = revisions.json()
    assert body["revisions"][0]["credential_available"] is True
    encoded = str(body).lower()
    assert "authorization" not in encoded
    assert "api_key" not in encoded
    assert "secret" not in encoded
