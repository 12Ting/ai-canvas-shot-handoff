from importlib import import_module
from importlib.util import find_spec

import pytest


def test_transcode_job_service_module_exists() -> None:
    assert find_spec("ai_canvas.application.transcode_job_service") is not None


def test_transcode_request_requires_explicit_confirmation() -> None:
    module = import_module("ai_canvas.application.transcode_job_service")
    requirement = module.TranscodeConfirmationRequired(
        confirmation_token="token",
        profile="preview_1080p",
        width=1920,
        height=1080,
        estimated_output_bytes=123,
    )

    assert requirement.confirmation_token == "token"
    assert requirement.profile == "preview_1080p"
    assert requirement.width == 1920
    assert requirement.height == 1080
    assert requirement.estimated_output_bytes == 123


def test_transcode_profile_does_not_upscale_or_exceed_30fps() -> None:
    module = import_module("ai_canvas.application.transcode_job_service")
    profile = module.resolve_preview_profile(
        "preview_1080p",
        width=800,
        height=600,
        frames_per_second=60.0,
        duration_seconds=2.0,
    )

    assert (profile.width, profile.height) == (800, 600)
    assert profile.frames_per_second == 30.0
    assert profile.audio_sample_rate == 48_000
    assert profile.audio_bitrate_kbps == 192


def test_unknown_transcode_profile_is_rejected() -> None:
    module = import_module("ai_canvas.application.transcode_job_service")

    with pytest.raises(ValueError):
        module.resolve_preview_profile(
            "arbitrary-ffmpeg-args",
            width=1920,
            height=1080,
            frames_per_second=24.0,
            duration_seconds=1.0,
        )


def test_preview_transcode_argv_is_fixed_and_shell_free(tmp_path) -> None:
    module = import_module("ai_canvas.application.transcode_job_service")
    profile = module.resolve_preview_profile(
        "preview_720p",
        width=1920,
        height=1080,
        frames_per_second=60.0,
        duration_seconds=2.0,
    )

    argv = module.build_preview_transcode_argv(
        executable="ffmpeg",
        source_path=tmp_path / "source.mov",
        destination_path=tmp_path / "output.mp4",
        profile=profile,
    )

    assert argv[:5] == ["ffmpeg", "-nostdin", "-v", "error", "-i"]
    assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "libx264"
    assert "-c:a" in argv and argv[argv.index("-c:a") + 1] == "aac"
    assert "-movflags" in argv and argv[argv.index("-movflags") + 1] == "+faststart"
    assert str(tmp_path / "source.mov") in argv
    assert argv[-1] == str(tmp_path / "output.mp4")
    assert not any(";" in part or "&&" in part for part in argv)
