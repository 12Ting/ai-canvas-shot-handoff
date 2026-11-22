import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from ai_canvas.config import AuthMode, Settings
from ai_canvas.main import create_app


def bind_listener(port: int = 0) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    return listener


def open_when_ready(health_url: str, launch_url: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(launch_url)
                    return
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    print(f"AI Canvas did not become ready: {health_url}", file=sys.stderr)


class ReleaseServer(uvicorn.Server):
    """Begin the short browser health check only after migrations and socket startup."""

    def __init__(self, config: uvicorn.Config, *, origin: str, launch_url: str | None):
        super().__init__(config)
        self.origin = origin
        self.launch_url = launch_url

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.launch_url:
            threading.Thread(
                target=open_when_ready,
                args=(f"{self.origin}/api/v1/health", self.launch_url),
                daemon=True,
            ).start()


def main() -> None:
    initial = Settings()
    if initial.auth_mode is AuthMode.DEVELOPMENT_AUTO_SESSION:
        raise ValueError("release launcher does not allow development-auto-session")
    listener = bind_listener(initial.listen_port)
    try:
        port = int(listener.getsockname()[1])
        origin = f"http://127.0.0.1:{port}"
        default_web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
        settings = initial.model_copy(
            update={
                "public_origin": origin,
                "web_dist": initial.web_dist or default_web_dist,
            }
        )
        app = create_app(settings)
        launch_url = f"{origin}/#bootstrap={app.state.session_manager.bootstrap_token}"
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            workers=1,
            log_level="info",
        )
        server = ReleaseServer(config, origin=origin, launch_url=launch_url if settings.open_browser else None)
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
