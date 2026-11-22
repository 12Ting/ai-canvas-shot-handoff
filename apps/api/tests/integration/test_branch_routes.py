from pathlib import Path

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _headers(app, client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert response.status_code == 200
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "Origin": "http://testserver",
    }


def test_branch_workspace_and_head_graph_survive_restart(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        headers = _headers(app, client)
        created = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Branches"},
        )
        assert created.status_code == 201
        project = created.json()
        workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        )
        assert workspace.status_code == 200
        body = workspace.json()
        assert body["selection"]["branch_id"] == project["selected_branch_id"]
        assert body["selection"]["graph_revision_id"] == project["active_revision_id"]
        assert body["branches"][0]["kind"] == "MAIN"
        assert body["branches"][0]["name"] == "主线"
        assert body["branches"][0]["created_at"].endswith(("Z", "+00:00"))
        branch_id = body["branches"][0]["id"]
        graph = client.get(
            f"/api/v1/projects/{project['id']}/branches/{branch_id}/graph",
            headers=headers,
        )
        assert graph.status_code == 200
        assert graph.json()["revision"]["revision_id"] == project["active_revision_id"]

    restarted = create_app(settings)
    with TestClient(restarted, base_url="http://testserver") as client:
        headers = _headers(restarted, client)
        restored = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        )
        assert restored.status_code == 200
        assert restored.json() == body
