from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ai_canvas.config import AuthMode
from ai_canvas.contracts.problems import RequestValidationProblem


router = APIRouter(prefix="/api/v1/session", tags=["session"])


class SessionResponse(BaseModel):
    access_token: str


class DevelopmentSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DevelopmentSessionForbidden(BaseModel):
    code: Literal[
        "development_session_disabled",
        "origin_required",
        "origin_denied",
        "loopback_peer_required",
        "literal_host_required",
    ]


class SessionStatusResponse(BaseModel):
    mode: AuthMode


def _literal_ipv4_host_is_valid(host: str) -> bool:
    if host == "127.0.0.1":
        return True
    prefix = "127.0.0.1:"
    if not host.startswith(prefix):
        return False
    port_text = host[len(prefix) :]
    if not 1 <= len(port_text) <= 5:
        return False
    if not port_text.isascii() or not port_text.isdecimal():
        return False
    try:
        port = int(port_text)
    except ValueError:
        return False
    return 1 <= port <= 65535


def _normalized_peer(request: Request) -> str | None:
    if request.client is None:
        return None
    if request.client.host == "::ffff:127.0.0.1":
        return "127.0.0.1"
    return request.client.host


@router.post(
    "/exchange",
    response_model=SessionResponse,
    responses={422: {"model": RequestValidationProblem}},
)
def exchange_session(
    request: Request,
    bootstrap: Annotated[str, Header(alias="X-Canvas-Bootstrap")],
) -> SessionResponse:
    return SessionResponse(access_token=request.app.state.session_manager.exchange(bootstrap))


@router.post(
    "/development",
    response_model=SessionResponse,
    responses={
        403: {"model": DevelopmentSessionForbidden},
        422: {"model": RequestValidationProblem},
    },
)
def issue_development_session(
    request: Request,
    _payload: DevelopmentSessionRequest,
) -> SessionResponse | JSONResponse:
    settings = request.app.state.settings
    if settings.auth_mode is not AuthMode.DEVELOPMENT_AUTO_SESSION:
        return JSONResponse(
            status_code=403,
            content={"code": "development_session_disabled"},
        )
    if _normalized_peer(request) != "127.0.0.1":
        return JSONResponse(
            status_code=403,
            content={"code": "loopback_peer_required"},
        )
    if not _literal_ipv4_host_is_valid(request.headers.get("host", "")):
        return JSONResponse(
            status_code=403,
            content={"code": "literal_host_required"},
        )
    allowed_origins = {settings.public_origin, *settings.development_origins}
    if request.headers.get("origin") not in allowed_origins:
        return JSONResponse(
            status_code=403,
            content={"code": "origin_denied"},
        )
    return SessionResponse(
        access_token=request.app.state.session_manager.issue_development_session()
    )


@router.get("/status", response_model=SessionStatusResponse)
def session_status(request: Request) -> SessionStatusResponse:
    return SessionStatusResponse(mode=request.app.state.settings.auth_mode)
