from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def test_storage_summary_and_gc_plan_errors_are_stable_for_the_web_client(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app)
        summary = client.get("/api/v1/storage/summary", headers=headers)
        plan = client.post("/api/v1/gc-plans", json={}, headers=headers)
        stale = client.post(
            f"/api/v1/gc-plans/{plan.json()['plan_id']}/confirm",
            json={"summary_sha256": "0" * 64},
            headers=headers,
        )
        missing = client.post(
            f"/api/v1/gc-plans/{uuid4()}/confirm",
            json={"summary_sha256": "0" * 64},
            headers=headers,
        )

    assert summary.status_code == 200
    assert summary.json()["workspace_quota_bytes"] == 100 * 1024**3
    assert summary.json()["managed_blob_bytes"] == 0
    assert plan.status_code == 200
    assert stale.status_code == 409
    assert stale.json() == {"code": "gc_plan_stale"}
    assert missing.status_code == 404
    assert missing.json() == {"code": "gc_plan_not_found"}
