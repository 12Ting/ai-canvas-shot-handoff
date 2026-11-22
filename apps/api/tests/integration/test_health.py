from pathlib import Path

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.main import create_app


def test_health_reports_service_version(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"name": "ai-canvas-core", "version": "0.1.0"}
