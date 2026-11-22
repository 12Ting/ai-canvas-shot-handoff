from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

import ai_canvas.main as canvas_main
from ai_canvas.config import Settings, release_data_dir
from ai_canvas.infrastructure.process_lock import DataDirectoryLock
from ai_canvas.main import create_app


class FailingInitializationHandle:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.closed = False

    def seek(self, offset: int, whence: int = 0) -> int:
        del offset
        target = "seek_end" if whence == 2 else "seek_start"
        if self.phase == target:
            raise OSError(self.phase)
        return 0

    def tell(self) -> int:
        if self.phase == "tell":
            raise OSError(self.phase)
        return 0

    def write(self, data: bytes) -> int:
        if self.phase == "write":
            raise OSError(self.phase)
        return len(data)

    def flush(self) -> None:
        if self.phase == "flush":
            raise OSError(self.phase)

    def close(self) -> None:
        self.closed = True


def test_rejects_a_second_live_owner_for_the_same_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    first = TestClient(create_app(settings), base_url="http://testserver")
    first.__enter__()
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            with TestClient(create_app(settings), base_url="http://testserver"):
                pass
    finally:
        first.__exit__(None, None, None)

    with TestClient(create_app(settings), base_url="http://testserver") as reopened:
        assert reopened.get("/api/v1/health").status_code == 200


def test_releases_data_lock_when_engine_dispose_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_create_engine = canvas_main.create_sqlite_engine

    def create_engine_with_failing_dispose(database_path: Path) -> Any:
        engine = original_create_engine(database_path)
        original_dispose = engine.dispose

        def failing_dispose() -> None:
            original_dispose()
            raise RuntimeError("dispose failed")

        monkeypatch.setattr(engine, "dispose", failing_dispose)
        return engine

    monkeypatch.setattr(
        canvas_main,
        "create_sqlite_engine",
        create_engine_with_failing_dispose,
    )
    client = TestClient(
        create_app(Settings(data_dir=tmp_path, public_origin="http://testserver")),
        base_url="http://testserver",
    )
    client.__enter__()
    with pytest.raises(RuntimeError, match="dispose failed") as shutdown_error:
        client.__exit__(None, None, None)

    data_lock = DataDirectoryLock(tmp_path)
    data_lock.acquire()
    data_lock.release()
    assert shutdown_error.value.args == ("dispose failed",)


@pytest.mark.parametrize(
    "phase",
    ["seek_end", "tell", "write", "flush", "seek_start"],
)
def test_closes_lock_handle_when_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    handle = FailingInitializationHandle(phase)

    def open_handle(_path: Path, *_args: object, **_kwargs: object) -> Any:
        return handle

    monkeypatch.setattr(Path, "open", open_handle)

    with pytest.raises(OSError, match=phase):
        DataDirectoryLock(tmp_path).acquire()

    assert handle.closed


def test_rejects_repeat_acquire_without_overwriting_live_handle(tmp_path: Path) -> None:
    data_lock = DataDirectoryLock(tmp_path)
    data_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            data_lock.acquire()
    finally:
        data_lock.release()


def test_release_is_idempotent_and_lock_object_can_be_reused(tmp_path: Path) -> None:
    data_lock = DataDirectoryLock(tmp_path)
    data_lock.acquire()
    data_lock.release()
    data_lock.release()
    data_lock.acquire()
    data_lock.release()


def test_release_data_defaults_under_local_app_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_CANVAS_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert release_data_dir() == tmp_path / "AI_Canvas"
    assert Settings().data_dir == tmp_path / "AI_Canvas"


def test_runtime_and_auth_modes_default_to_release_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_CANVAS_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("AI_CANVAS_AUTH_MODE", raising=False)
    from ai_canvas.config import AuthMode, RuntimeProfile

    settings = Settings(data_dir=tmp_path)

    assert settings.runtime_profile is RuntimeProfile.RELEASE
    assert settings.auth_mode is AuthMode.SESSION


def _automatic_settings_kwargs(tmp_path: Path) -> dict[str, object]:
    return {
        "data_dir": tmp_path,
        "runtime_profile": "development",
        "auth_mode": "development-auto-session",
        "public_origin": "http://127.0.0.1:8765",
        "development_origins": ("http://127.0.0.1:5173",),
        "allowed_hosts": ("127.0.0.1",),
    }


def test_release_profile_rejects_development_auto_session(tmp_path: Path) -> None:
    settings = _automatic_settings_kwargs(tmp_path)
    settings["runtime_profile"] = "release"

    with pytest.raises(ValueError, match="development-auto-session"):
        Settings(**settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_origin", "http://localhost:8765"),
        ("public_origin", "http://127.0.0.2:8765"),
        ("public_origin", "http://0.0.0.0:8765"),
        ("public_origin", "http://*:8765"),
        ("public_origin", "http://127.0.0.1"),
        ("public_origin", "http://127.0.0.1:0"),
        ("public_origin", "http://127.0.0.1:65536"),
        ("public_origin", "http://127.0.0.1:not-a-port"),
        ("public_origin", "https://127.0.0.1:8765"),
        ("public_origin", "http://user:password@127.0.0.1:8765"),
        ("public_origin", "http://127.0.0.1:8765/api"),
        ("public_origin", "http://127.0.0.1:8765?debug=1"),
        ("public_origin", "http://127.0.0.1:8765#debug"),
        (
            "development_origins",
            ("http://127.0.0.1:5173", "http://localhost:5174"),
        ),
    ],
)
def test_development_auto_session_rejects_unsafe_origins(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    settings = _automatic_settings_kwargs(tmp_path)
    settings[field] = value

    with pytest.raises(ValueError, match="127.0.0.1"):
        Settings(**settings)


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        (),
        ("localhost",),
        ("*",),
        ("127.0.0.2",),
        ("127.0.0.1", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_development_auto_session_requires_literal_single_allowed_host(
    tmp_path: Path,
    allowed_hosts: tuple[str, ...],
) -> None:
    settings = _automatic_settings_kwargs(tmp_path)
    settings["allowed_hosts"] = allowed_hosts

    with pytest.raises(ValueError, match="allowed_hosts"):
        Settings(**settings)


def test_development_auto_session_accepts_root_slash_origins(tmp_path: Path) -> None:
    settings = _automatic_settings_kwargs(tmp_path)
    settings["public_origin"] = "http://127.0.0.1:8765/"
    settings["development_origins"] = ("http://127.0.0.1:5173/",)

    resolved = Settings(**settings)

    assert resolved.auth_mode.value == "development-auto-session"
    assert resolved.public_origin == "http://127.0.0.1:8765"
    assert resolved.development_origins == ("http://127.0.0.1:5173",)


def test_normal_session_does_not_normalize_configured_origins(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        public_origin="http://testserver/",
        development_origins=("http://127.0.0.1:5173/",),
    )

    assert settings.public_origin == "http://testserver/"
    assert settings.development_origins == ("http://127.0.0.1:5173/",)


def test_create_app_rejects_unvalidated_release_auto_session(tmp_path: Path) -> None:
    from ai_canvas.config import AuthMode, RuntimeProfile

    valid = Settings(**_automatic_settings_kwargs(tmp_path))
    invalid = valid.model_copy(
        update={
            "runtime_profile": RuntimeProfile.RELEASE,
            "auth_mode": AuthMode.DEVELOPMENT_AUTO_SESSION,
        }
    )

    with pytest.raises(ValueError, match="development-auto-session"):
        create_app(invalid)


def test_health_echoes_only_the_injected_instance_marker(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            instance_id="expected-child",
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/v1/health")
    assert response.json() == {"name": "ai-canvas-core", "version": "0.1.0"}
    assert response.headers["X-AI-Canvas-Instance"] == "expected-child"


def test_injected_bootstrap_is_used_without_being_returned(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            bootstrap_token="known-development-bootstrap",
        )
    )
    assert app.state.session_manager.bootstrap_token == "known-development-bootstrap"

    with TestClient(app, base_url="http://testserver") as client:
        response = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": "known-development-bootstrap",
                "Origin": "http://testserver",
            },
        )

    assert response.status_code == 200
    assert "known-development-bootstrap" not in response.text
