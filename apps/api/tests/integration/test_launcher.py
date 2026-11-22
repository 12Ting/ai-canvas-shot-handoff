from http.client import RemoteDisconnected
import socket

import pytest

import ai_canvas.launcher as launcher
from ai_canvas.launcher import bind_listener


class ReadyResponse:
    status = 200

    def __enter__(self) -> "ReadyResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_random_listener_keeps_its_port_reserved() -> None:
    listener = bind_listener()
    challenger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        port = int(listener.getsockname()[1])
        assert port > 0
        with pytest.raises(OSError):
            challenger.bind(("127.0.0.1", port))
    finally:
        challenger.close()
        listener.close()


@pytest.mark.parametrize(
    "connection_error",
    [ConnectionResetError("reset"), RemoteDisconnected("remote disconnected")],
)
def test_browser_open_retries_transient_connection_errors_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    connection_error: OSError,
) -> None:
    outcomes: list[BaseException | ReadyResponse] = [connection_error, ReadyResponse()]
    sleeps: list[float] = []
    opened: list[str] = []

    def fake_urlopen(_url: str, timeout: float) -> ReadyResponse:
        assert timeout == 0.5
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(launcher, "urlopen", fake_urlopen)
    monkeypatch.setattr(launcher.time, "sleep", sleeps.append)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    launcher.open_when_ready("http://health", "http://launch")

    assert sleeps == [0.1]
    assert opened == ["http://launch"]


def test_browser_open_does_not_swallow_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(_url: str, timeout: float) -> ReadyResponse:
        del timeout
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "urlopen", interrupt)

    with pytest.raises(KeyboardInterrupt):
        launcher.open_when_ready("http://health", "http://launch")


def test_release_launcher_rejects_auto_session_before_any_side_effect(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_canvas.config import AuthMode, RuntimeProfile, Settings

    valid = Settings(
        data_dir=tmp_path,
        runtime_profile="development",
        auth_mode="development-auto-session",
        public_origin="http://127.0.0.1:8765",
        development_origins=("http://127.0.0.1:5173",),
        allowed_hosts=("127.0.0.1",),
    )
    invalid = valid.model_copy(
        update={
            "runtime_profile": RuntimeProfile.RELEASE,
            "auth_mode": AuthMode.DEVELOPMENT_AUTO_SESSION,
        }
    )
    side_effects: list[str] = []

    monkeypatch.setattr(launcher, "Settings", lambda: invalid)
    monkeypatch.setattr(
        launcher,
        "bind_listener",
        lambda *_args, **_kwargs: side_effects.append("bind"),
    )
    monkeypatch.setattr(
        launcher.webbrowser,
        "open",
        lambda *_args, **_kwargs: side_effects.append("browser"),
    )
    monkeypatch.setattr(
        launcher.uvicorn,
        "Server",
        lambda *_args, **_kwargs: side_effects.append("server"),
    )

    with pytest.raises(ValueError, match="development-auto-session"):
        launcher.main()

    assert side_effects == []


def test_release_browser_waits_until_application_and_socket_are_ready(monkeypatch):
    import asyncio
    from threading import Event

    opened = Event()
    order = []

    async def simulate_migrations_and_listen(server, sockets=None):
        assert not opened.is_set()
        order.append("migrated")
        server.started = True

    def capture_browser(health, launch):
        assert health == "http://local/api/v1/health"
        assert launch == "http://local/#bootstrap=test"
        order.append("browser")
        opened.set()

    monkeypatch.setattr(launcher.uvicorn.Server, "startup", simulate_migrations_and_listen)
    monkeypatch.setattr(launcher, "open_when_ready", capture_browser)
    server = launcher.ReleaseServer(launcher.uvicorn.Config("example:app"), origin="http://local",
                                    launch_url="http://local/#bootstrap=test")
    asyncio.run(server.startup())
    assert opened.wait(2)
    assert order == ["migrated", "browser"]
