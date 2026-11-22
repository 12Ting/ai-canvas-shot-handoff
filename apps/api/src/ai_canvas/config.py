import os
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def release_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    return Path(local_app_data, "AI_Canvas") if local_app_data else Path(".local")


class RuntimeProfile(str, Enum):
    RELEASE = "release"
    DEVELOPMENT = "development"


class AuthMode(str, Enum):
    SESSION = "session"
    DEVELOPMENT_AUTO_SESSION = "development-auto-session"


def _validate_development_origin(origin: str, field_name: str) -> str:
    requirement = (
        f"{field_name} must be an http://127.0.0.1:<port> origin without "
        "credentials, a non-root path, query, or fragment"
    )
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as error:
        raise ValueError(requirement) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(requirement)
    return f"http://127.0.0.1:{port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_CANVAS_", extra="ignore")

    data_dir: Path = Field(default_factory=release_data_dir)
    handoff_parent: Path | None = None
    runtime_profile: RuntimeProfile = RuntimeProfile.RELEASE
    auth_mode: AuthMode = AuthMode.SESSION
    public_origin: str = "http://127.0.0.1:8765"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    development_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)
    bootstrap_token: str | None = None
    web_dist: Path | None = None
    listen_port: int = Field(default=0, ge=0, le=65535)
    graph_revision_body_limit_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    resource_metadata_body_limit_bytes: int = Field(default=1024 * 1024, ge=1)
    artifact_image_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    artifact_video_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    artifact_upload_chunk_bytes: int = Field(default=1024 * 1024, ge=1)
    artifact_upload_idle_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    artifact_upload_total_timeout_seconds: float = Field(default=900, gt=0, allow_inf_nan=False)
    artifact_probe_metadata_timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    artifact_probe_full_scan_timeout_seconds: float = Field(
        default=120,
        gt=0,
        allow_inf_nan=False,
    )
    artifact_probe_output_limit_bytes: int = Field(default=256 * 1024, ge=1)
    artifact_image_max_pixels: int = Field(default=50_000_000, ge=1)
    artifact_image_max_side: int = Field(default=16_384, ge=1)
    artifact_video_max_duration_seconds: float = Field(
        default=600,
        gt=0,
        allow_inf_nan=False,
    )
    artifact_video_max_side: int = Field(default=4_096, ge=1)
    artifact_video_max_fps: float = Field(default=120, gt=0, allow_inf_nan=False)
    artifact_staging_cleanup_age_seconds: float = Field(
        default=3_600,
        gt=0,
        allow_inf_nan=False,
    )
    media_single_file_max_bytes: int = Field(default=10 * 1024**3, ge=1)
    workspace_quota_bytes: int = Field(default=100 * 1024**3, ge=1)
    media_large_upload_part_bytes: int = Field(default=8 * 1024**2, ge=1)
    media_physical_free_floor_bytes: int = Field(default=10 * 1024**3, ge=0)
    media_reservation_multiplier: float = Field(default=1.25, ge=1.0)
    media_session_idle_seconds: int = Field(default=10 * 60, ge=1)
    media_session_absolute_seconds: int = Field(default=60 * 60, ge=1)
    media_quarantine_days: int = Field(default=7, ge=1)
    media_transcode_timeout_seconds: float = Field(default=30 * 60, gt=0, allow_inf_nan=False)
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    open_browser: bool = True
    instance_id: str | None = None
    canvas_agent_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_development_auto_session(self) -> "Settings":
        if self.auth_mode is not AuthMode.DEVELOPMENT_AUTO_SESSION:
            return self
        if self.runtime_profile is not RuntimeProfile.DEVELOPMENT:
            raise ValueError(
                "development-auto-session requires the development runtime profile"
            )
        if self.allowed_hosts != ("127.0.0.1",):
            raise ValueError(
                "development-auto-session allowed_hosts must equal ('127.0.0.1',)"
            )
        self.public_origin = _validate_development_origin(
            self.public_origin,
            "public_origin",
        )
        self.development_origins = tuple(
            _validate_development_origin(origin, f"development_origins[{index}]")
            for index, origin in enumerate(self.development_origins)
        )
        return self
