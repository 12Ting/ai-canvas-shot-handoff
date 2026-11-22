from __future__ import annotations

import base64
import copy
import errno
import io
import json
import os
import shutil
import subprocess
import threading
import time
import warnings
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image, features

from ai_canvas.contracts.artifact import ArtifactImportFailureCode
from ai_canvas.domain.media import probe as probe_module
from ai_canvas.domain.media.probe import (
    CorruptMediaError,
    MediaProbeOutputLimitError,
    MediaProbeTimeoutError,
    MediaProbeUnavailableError,
    MediaStorageIOError,
    UnsafeMediaPathError,
    UnsupportedMediaError,
    probe_image,
    probe_video,
    resolve_ffprobe,
    run_ffprobe_bounded,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return base64.b64decode((FIXTURES / f"{name}.b64").read_text(encoding="ascii"))


def mp4_with_major_brand(major_brand: bytes, *, compatible_brand: bytes | None = None) -> bytes:
    assert len(major_brand) == 4
    if compatible_brand is not None:
        assert len(compatible_brand) == 4
    payload = bytearray(fixture_bytes("tiny_mp4"))
    assert payload[4:8] == b"ftyp"
    box_size = int.from_bytes(payload[:4], "big")
    assert box_size >= 20 and box_size % 4 == 0
    payload[8:12] = major_brand
    if compatible_brand is not None:
        for offset in range(16, box_size, 4):
            payload[offset : offset + 4] = compatible_brand
    return bytes(payload)


def quicktime_mov_bytes(*, compatible_brand: bytes = b"qt  ") -> bytes:
    return mp4_with_major_brand(b"qt  ", compatible_brand=compatible_brand)


def matroska_header_bytes() -> bytes:
    doctype = b"\x42\x82\x88matroska"
    return b"\x1aE\xdf\xa3" + bytes([0x80 + len(doctype)]) + doctype


def installed_ffprobe() -> Path:
    executable = shutil.which("ffprobe")
    assert executable is not None
    return Path(executable)


def staged_path(tmp_path: Path, content: bytes) -> tuple[Path, Path]:
    staging_root = tmp_path / "artifacts" / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    path = staging_root / f"{uuid4()}.part"
    path.write_bytes(content)
    return path, staging_root


def create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def assert_media_storage_io_error(error: Exception, unsafe_path: Path) -> None:
    assert isinstance(error, MediaStorageIOError)
    assert error.failure_code is ArtifactImportFailureCode.UPLOAD_INTERRUPTED
    assert str(error) == "media storage is temporarily unavailable"
    assert str(unsafe_path) not in str(error)


def encoded_image(format_name: str) -> bytes:
    if format_name == "PNG":
        return fixture_bytes("tiny_png")
    output = io.BytesIO()
    Image.new("RGB", (4, 3), (51, 102, 153)).save(output, format=format_name)
    return output.getvalue()


@pytest.mark.parametrize(
    ("format_name", "media_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_valid_static_images_are_fully_verified_and_loaded(
    tmp_path: Path,
    format_name: str,
    media_type: str,
) -> None:
    path, root = staged_path(tmp_path, encoded_image(format_name))

    result = probe_image(path, staging_root=root)

    assert result.kind == "image"
    assert result.media_type == media_type
    assert result.metadata.model_dump() == {"kind": "image", "width": 4, "height": 3}


def test_image_probe_accepts_a_nonstandard_canonical_uuid_staging_name(tmp_path: Path) -> None:
    staging_root = tmp_path / "artifacts" / ".staging"
    staging_root.mkdir(parents=True)
    import_id = UUID("12345678-1234-f678-0234-567812345678")
    path = staging_root / f"{import_id}.part"
    path.write_bytes(fixture_bytes("tiny_png"))

    result = probe_image(path, staging_root=staging_root)

    assert result.kind == "image"
    assert result.media_type == "image/png"


def test_image_probe_restricts_plugins_on_both_open_calls(tmp_path: Path, monkeypatch) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    original_open = probe_module.Image.open
    observed_formats: list[tuple[str, ...] | None] = []

    def recording_open(*args, **kwargs):
        observed_formats.append(kwargs.get("formats"))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(probe_module.Image, "open", recording_open)

    probe_image(path, staging_root=root)

    assert observed_formats == [("JPEG", "PNG", "WEBP"), ("JPEG", "PNG", "WEBP")]


def encoded_animated_image(format_name: str) -> bytes:
    output = io.BytesIO()
    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "blue")
    first.save(output, format=format_name, save_all=True, append_images=[second], duration=100, loop=0)
    return output.getvalue()


def assert_animated_image_is_rejected(
    tmp_path: Path,
    *,
    format_name: str,
) -> None:
    content = encoded_animated_image(format_name)
    with Image.open(io.BytesIO(content)) as generated:
        assert generated.n_frames == 2, f"Pillow did not create a real animated {format_name} fixture"
    path, root = staged_path(tmp_path, content)

    with pytest.raises(UnsupportedMediaError) as captured:
        probe_image(path, staging_root=root)

    assert captured.value.failure_code is ArtifactImportFailureCode.UNSUPPORTED_MEDIA


def test_animated_png_is_rejected_as_unsupported_media(tmp_path: Path) -> None:
    assert_animated_image_is_rejected(tmp_path, format_name="PNG")


def test_animated_gif_is_rejected_as_unsupported_media(tmp_path: Path) -> None:
    assert_animated_image_is_rejected(tmp_path, format_name="GIF")


def animated_webp_round_trip_supported() -> bool:
    try:
        content = encoded_animated_image("WEBP")
        with Image.open(io.BytesIO(content)) as generated:
            if generated.format != "WEBP" or generated.n_frames != 2:
                return False
            generated.seek(1)
            generated.load()
    except (AttributeError, KeyError, OSError, ValueError):
        return False
    return True


def animated_webp_supported() -> bool:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Unknown feature 'webp_anim'",
            category=UserWarning,
        )
        legacy_capability = features.check("webp_anim")
    return legacy_capability or animated_webp_round_trip_supported()


WEBP_ANIMATION_SUPPORTED = animated_webp_supported()


@pytest.mark.skipif(
    not WEBP_ANIMATION_SUPPORTED,
    reason="the installed Pillow build cannot encode and decode a two-frame WebP",
)
def test_animated_webp_is_rejected_as_unsupported_media(tmp_path: Path) -> None:
    assert_animated_image_is_rejected(tmp_path, format_name="WEBP")


def test_truncated_image_is_rejected_as_corrupt(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png")[:-12])

    with pytest.raises(CorruptMediaError) as captured:
        probe_image(path, staging_root=root)

    assert captured.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA


def test_decompression_bomb_warning_or_error_is_rejected(tmp_path: Path, monkeypatch) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    monkeypatch.setattr(probe_module.Image, "MAX_IMAGE_PIXELS", 1)

    with pytest.raises(UnsupportedMediaError, match="pixel"):
        probe_image(path, staging_root=root)


def test_image_dimensions_and_pixel_count_are_bounded(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))

    with pytest.raises(UnsupportedMediaError, match="dimensions"):
        probe_image(path, staging_root=root, max_side=3)
    with pytest.raises(UnsupportedMediaError, match="pixels"):
        probe_image(path, staging_root=root, max_pixels=11)


def test_image_probe_rejects_non_generated_path_before_open(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "attacker-controlled.png"
    path.write_bytes(fixture_bytes("tiny_png"))
    opened = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("unsafe path reached Pillow")

    monkeypatch.setattr(probe_module.Image, "open", forbidden_open)
    with pytest.raises(UnsafeMediaPathError):
        probe_image(path, staging_root=tmp_path / "artifacts" / ".staging")
    assert opened is False


def test_image_probe_missing_canonical_part_is_retryable_storage_io(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / ".staging"
    root.mkdir(parents=True)
    path = root / f"{uuid4()}.part"

    with pytest.raises(Exception) as captured:
        probe_image(path, staging_root=root)

    assert_media_storage_io_error(captured.value, path)


def test_image_probe_missing_trusted_directory_chain_is_retryable_storage_io(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts" / ".staging"
    path = root / f"{uuid4()}.part"

    with pytest.raises(Exception) as captured:
        probe_image(path, staging_root=root)

    assert_media_storage_io_error(captured.value, path)


@pytest.mark.parametrize("path_kind", ["invalid_uuid", "foreign", "noncanonical_uuid"])
def test_missing_untrusted_staging_paths_remain_corrupt_media(
    tmp_path: Path,
    path_kind: str,
) -> None:
    root = tmp_path / "artifacts" / ".staging"
    root.mkdir(parents=True)
    if path_kind == "invalid_uuid":
        path = root / "not-a-uuid.part"
    elif path_kind == "foreign":
        path = tmp_path / "elsewhere" / f"{uuid4()}.part"
    else:
        path = root / f"{str(uuid4()).upper()}.part"

    with pytest.raises(UnsafeMediaPathError) as captured:
        probe_image(path, staging_root=root)

    assert captured.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA


def test_image_probe_translates_staging_lstat_permission_without_leaking_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    real_lstat = Path.lstat

    def denied_lstat(candidate: Path):
        if candidate == path:
            raise PermissionError(errno.EACCES, "lstat denied", str(candidate))
        return real_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    with pytest.raises(Exception) as captured:
        probe_image(path, staging_root=root)

    assert_media_storage_io_error(captured.value, path)


def test_image_probe_translates_sniff_read_permission_without_leaking_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    real_open = Path.open

    def denied_open(candidate: Path, *args, **kwargs):
        if candidate == path and args and args[0] == "rb":
            raise PermissionError(errno.EACCES, "read denied", str(candidate))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)

    with pytest.raises(Exception) as captured:
        probe_image(path, staging_root=root)

    assert_media_storage_io_error(captured.value, path)


def test_image_decoder_errno_bearing_oserror_is_retryable_storage_io(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    monkeypatch.setattr(probe_module, "_sniff_image_format", lambda _path: "PNG")

    def failed_decoder(*_args, **_kwargs):
        raise OSError(errno.EIO, "decoder read failed", str(path))

    monkeypatch.setattr(probe_module.Image, "open", failed_decoder)

    with pytest.raises(Exception) as captured:
        probe_image(path, staging_root=root)

    assert_media_storage_io_error(captured.value, path)


def test_image_decoder_oserror_without_errno_remains_corrupt_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_png"))
    monkeypatch.setattr(probe_module, "_sniff_image_format", lambda _path: "PNG")

    def rejected_bytes(*_args, **_kwargs):
        raise OSError("decoder rejected corrupt bytes")

    monkeypatch.setattr(probe_module.Image, "open", rejected_bytes)

    with pytest.raises(CorruptMediaError) as captured:
        probe_image(path, staging_root=root)

    assert captured.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA


def test_image_probe_rejects_artifacts_ancestor_reparse_without_external_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    external_staging = external / ".staging"
    data_dir.mkdir()
    external_staging.mkdir(parents=True)
    part = external_staging / f"{uuid4()}.part"
    part.write_bytes(fixture_bytes("tiny_png"))
    artifacts_link = data_dir / "artifacts"
    create_directory_link(artifacts_link, external)
    opened = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("external media reached Pillow")

    monkeypatch.setattr(probe_module.Image, "open", forbidden_open)
    try:
        with pytest.raises(UnsafeMediaPathError):
            probe_image(
                data_dir / "artifacts" / ".staging" / part.name,
                staging_root=data_dir / "artifacts" / ".staging",
            )
        assert opened is False
        assert part.read_bytes() == fixture_bytes("tiny_png")
    finally:
        remove_directory_link(artifacts_link)


def test_real_mp4_and_webm_metadata_are_accepted_after_full_scan(tmp_path: Path) -> None:
    expected = {
        "tiny_mp4": ("video/mp4", 25.0),
        "tiny_webm": ("video/webm", 5.0),
    }
    assert shutil.which("ffprobe") is not None

    for fixture_name, (media_type, fps) in expected.items():
        path, root = staged_path(tmp_path / fixture_name, fixture_bytes(fixture_name))
        result = probe_video(path, staging_root=root)
        assert result.kind == "video"
        assert result.media_type == media_type
        assert result.metadata.width == 16
        assert result.metadata.height == 16
        assert result.metadata.duration_seconds == pytest.approx(0.2)
        assert result.metadata.frames_per_second == pytest.approx(fps)


def test_real_quicktime_branded_mov_fixture_is_accepted_after_full_scan(tmp_path: Path) -> None:
    assert shutil.which("ffprobe") is not None
    path, root = staged_path(tmp_path, quicktime_mov_bytes())

    result = probe_video(path, staging_root=root)

    assert result.kind == "video"
    assert result.media_type == "video/quicktime"
    assert result.metadata.width == 16
    assert result.metadata.height == 16
    assert result.metadata.duration_seconds == pytest.approx(0.2)
    assert result.metadata.frames_per_second == pytest.approx(25.0)


def base_mp4_metadata() -> dict:
    return {
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "pix_fmt": "yuv420p",
                "bits_per_raw_sample": "8",
                "width": 16,
                "height": 16,
                "r_frame_rate": "5/1",
                "avg_frame_rate": "5/1",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "0.200000",
            "tags": {"major_brand": "isom"},
        },
    }


def successful_scan() -> dict:
    return {"streams": [{"index": 0, "nb_read_frames": "1", "nb_read_packets": "1"}]}


def quicktime_mov_metadata_with_aac() -> dict:
    metadata = base_mp4_metadata()
    metadata["format"]["tags"]["major_brand"] = "qt  "
    metadata["streams"].append(
        {
            "index": 1,
            "codec_name": "aac",
            "codec_type": "audio",
        }
    )
    return metadata


def successful_scan_with_aac() -> dict:
    return {
        "streams": [
            {"index": 0, "codec_type": "video", "nb_read_frames": "1", "nb_read_packets": "1"},
            {"index": 1, "codec_type": "audio", "nb_read_packets": "1"},
        ]
    }


def scripted_runner(metadata: dict, scan: dict | None = None):
    calls: list[tuple[list[str], float, int]] = []
    results = [metadata, successful_scan() if scan is None else scan]

    def runner(argv: list[str], *, timeout: float, output_limit: int) -> bytes:
        calls.append((argv, timeout, output_limit))
        return json.dumps(results[len(calls) - 1]).encode()

    return runner, calls


def test_quicktime_branded_h264_aac_mov_runs_full_scan_and_returns_video_quicktime(
    tmp_path: Path,
) -> None:
    path, root = staged_path(tmp_path, quicktime_mov_bytes())
    runner, calls = scripted_runner(quicktime_mov_metadata_with_aac(), successful_scan_with_aac())

    result = probe_video(
        path,
        staging_root=root,
        ffprobe_path=installed_ffprobe(),
        runner=runner,
    )

    assert result.kind == "video"
    assert result.media_type == "video/quicktime"
    assert len(calls) == 2
    assert "-show_streams" in calls[0][0] and "-show_format" in calls[0][0]
    assert "-count_frames" in calls[1][0] and "-count_packets" in calls[1][0]


def test_video_probe_runs_bounded_metadata_and_full_file_frame_packet_passes(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_mp4"))
    runner, calls = scripted_runner(base_mp4_metadata())

    result = probe_video(
        path,
        staging_root=root,
        ffprobe_path=installed_ffprobe(),
        metadata_timeout=10.0,
        full_scan_timeout=120.0,
        output_limit=256 * 1024,
        runner=runner,
    )

    assert result.media_type == "video/mp4"
    assert len(calls) == 2
    metadata_argv, metadata_timeout, metadata_limit = calls[0]
    scan_argv, scan_timeout, scan_limit = calls[1]
    assert Path(metadata_argv[0]) == installed_ffprobe().resolve()
    assert "-show_streams" in metadata_argv and "-show_format" in metadata_argv
    assert "-count_frames" in scan_argv and "-count_packets" in scan_argv
    assert "-show_frames" not in scan_argv and "-show_packets" not in scan_argv
    assert metadata_timeout == 10.0 and scan_timeout == 120.0
    assert metadata_limit == scan_limit == 256 * 1024
    for argv, _timeout, _limit in calls:
        whitelist = argv.index("-protocol_whitelist")
        assert argv[whitelist + 1] == "file"
        assert argv[-1] == str(path)


def invalid_metadata_cases() -> list[tuple[str, dict]]:
    cases: list[tuple[str, dict]] = []

    def changed(label: str, **values) -> None:
        payload = base_mp4_metadata()
        payload["streams"][0].update(values)
        cases.append((label, payload))

    changed("wrong video codec", codec_name="hevc")
    changed("ten bit video", pix_fmt="yuv420p10le", bits_per_raw_sample="10")
    changed("oversized width", width=4097)
    changed("oversized height", height=4097)
    changed("fps over limit", avg_frame_rate="121/1", r_frame_rate="121/1")
    changed("invalid fps", avg_frame_rate="0/0", r_frame_rate="0/0")

    two_video = base_mp4_metadata()
    second = copy.deepcopy(two_video["streams"][0])
    second["index"] = 1
    two_video["streams"].append(second)
    cases.append(("multiple video streams", two_video))

    subtitle = base_mp4_metadata()
    subtitle["streams"].append({"index": 1, "codec_type": "subtitle", "codec_name": "mov_text"})
    cases.append(("subtitle stream", subtitle))

    bad_audio = base_mp4_metadata()
    bad_audio["streams"].append({"index": 1, "codec_type": "audio", "codec_name": "mp3"})
    cases.append(("wrong audio codec", bad_audio))

    wrong_container = base_mp4_metadata()
    wrong_container["format"]["format_name"] = "matroska,webm"
    cases.append(("wrong container", wrong_container))

    too_long = base_mp4_metadata()
    too_long["format"]["duration"] = "600.001"
    cases.append(("duration over limit", too_long))
    return cases


def quicktime_invalid_metadata_cases() -> list[tuple[str, dict, str]]:
    cases: list[tuple[str, dict, str]] = []

    def changed(label: str, error_message: str, **values) -> None:
        payload = quicktime_mov_metadata_with_aac()
        payload["streams"][0].update(values)
        cases.append((label, payload, error_message))

    changed("HEVC video", "video codec", codec_name="hevc")
    changed("ProRes video", "video codec", codec_name="prores")
    changed("ten bit video", "8-bit 4:2:0 pixels", pix_fmt="yuv420p10le", bits_per_raw_sample="10")

    multiple_video = quicktime_mov_metadata_with_aac()
    second_video = copy.deepcopy(multiple_video["streams"][0])
    second_video["index"] = 2
    multiple_video["streams"].append(second_video)
    cases.append(("multiple video tracks", multiple_video, "exactly one video stream"))

    multiple_audio = quicktime_mov_metadata_with_aac()
    second_audio = copy.deepcopy(multiple_audio["streams"][1])
    second_audio["index"] = 2
    multiple_audio["streams"].append(second_audio)
    cases.append(("multiple audio tracks", multiple_audio, "at most one audio stream"))

    for stream_type in ("subtitle", "data", "attachment"):
        forbidden_stream = quicktime_mov_metadata_with_aac()
        forbidden_stream["streams"].append(
            {"index": 2, "codec_type": stream_type, "codec_name": "unsupported"}
        )
        cases.append((f"{stream_type} track", forbidden_stream, "forbidden stream type"))
    return cases


@pytest.mark.parametrize(("label", "metadata", "error_message"), quicktime_invalid_metadata_cases())
def test_quicktime_branded_mov_keeps_existing_codec_pixel_and_stream_restrictions(
    tmp_path: Path,
    label: str,
    metadata: dict,
    error_message: str,
) -> None:
    path, root = staged_path(tmp_path, quicktime_mov_bytes())
    runner, calls = scripted_runner(metadata, successful_scan_with_aac())

    with pytest.raises(UnsupportedMediaError, match=error_message):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)

    assert len(calls) == 1, label


def test_unknown_major_brand_with_quicktime_compatible_brand_remains_rejected(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, mp4_with_major_brand(b"bad!", compatible_brand=b"qt  "))
    runner, calls = scripted_runner(quicktime_mov_metadata_with_aac(), successful_scan_with_aac())

    with pytest.raises(UnsupportedMediaError, match="container is unsupported"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)

    assert calls == []


def test_quicktime_major_brand_takes_priority_over_mp4_compatible_brand(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, quicktime_mov_bytes(compatible_brand=b"mp42"))
    runner, calls = scripted_runner(quicktime_mov_metadata_with_aac(), successful_scan_with_aac())

    result = probe_video(
        path,
        staging_root=root,
        ffprobe_path=installed_ffprobe(),
        runner=runner,
    )

    assert result.media_type == "video/quicktime"
    assert len(calls) == 2


def test_mp4_major_brand_is_not_overridden_by_quicktime_compatible_brand(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, mp4_with_major_brand(b"isom", compatible_brand=b"qt  "))
    runner, calls = scripted_runner(base_mp4_metadata())

    result = probe_video(
        path,
        staging_root=root,
        ffprobe_path=installed_ffprobe(),
        runner=runner,
    )

    assert result.media_type == "video/mp4"
    assert len(calls) == 2


def test_matroska_doctype_returns_video_x_matroska_after_full_scan(tmp_path: Path) -> None:
    metadata = base_mp4_metadata()
    metadata["format"]["format_name"] = "matroska"
    path, root = staged_path(tmp_path, matroska_header_bytes())
    runner, calls = scripted_runner(metadata)

    result = probe_video(
        path,
        staging_root=root,
        ffprobe_path=installed_ffprobe(),
        runner=runner,
    )

    assert result.media_type == "video/x-matroska"
    assert len(calls) == 2


@pytest.mark.parametrize(("label", "metadata"), invalid_metadata_cases())
def test_wrong_video_container_codec_streams_dimensions_duration_and_fps_are_rejected(
    tmp_path: Path,
    label: str,
    metadata: dict,
) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_mp4"))
    runner, _calls = scripted_runner(metadata)

    with pytest.raises(UnsupportedMediaError, match="video|stream|codec|container|dimension|duration|fps"):
        probe_video(
            path,
            staging_root=root,
            ffprobe_path=installed_ffprobe(),
            runner=runner,
        )


def test_malformed_ffprobe_duration_is_a_typed_corrupt_media_error(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_mp4"))
    metadata = base_mp4_metadata()
    metadata["format"]["duration"] = []
    runner, _calls = scripted_runner(metadata)

    with pytest.raises(CorruptMediaError, match="duration"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)


def test_invalid_full_scan_counts_are_rejected(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_mp4"))
    runner, calls = scripted_runner(
        base_mp4_metadata(),
        {"streams": [{"index": 0, "nb_read_frames": "0", "nb_read_packets": "1"}]},
    )

    with pytest.raises(CorruptMediaError, match="scan"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)

    assert len(calls) == 2, "metadata-only validation must not accept the file"


def test_full_scan_rejects_duplicate_stream_indexes(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, fixture_bytes("tiny_mp4"))
    duplicate = {"index": 0, "nb_read_frames": "1", "nb_read_packets": "1"}
    runner, _calls = scripted_runner(
        base_mp4_metadata(),
        {"streams": [duplicate, dict(duplicate)]},
    )

    with pytest.raises(CorruptMediaError, match="scan"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)


def test_mid_file_video_corruption_is_detected_by_real_full_scan(tmp_path: Path) -> None:
    content = bytearray(fixture_bytes("tiny_mp4"))
    packet_prefix = bytes.fromhex("00000002093000000005")
    last_packet = content.rfind(packet_prefix)
    later_packet = content.rfind(packet_prefix, 0, last_packet)
    assert later_packet > content.index(b"mdat") + 4
    nal_header = later_packet + len(packet_prefix)
    assert content[nal_header] == 0x41
    content[nal_header] = 0xFF
    path, root = staged_path(tmp_path, bytes(content))
    calls: list[list[str]] = []
    completed: list[list[str]] = []

    def recording_runner(argv: list[str], *, timeout: float, output_limit: int) -> bytes:
        calls.append(argv)
        output = run_ffprobe_bounded(argv, timeout=timeout, output_limit=output_limit)
        completed.append(argv)
        return output

    with pytest.raises(CorruptMediaError, match="decoding"):
        probe_video(
            path,
            staging_root=root,
            ffprobe_path=installed_ffprobe(),
            runner=recording_runner,
        )

    assert len(calls) == 2
    assert completed == [calls[0]], "metadata must succeed cleanly before the full scan fails"
    assert "-show_streams" in calls[0] and "-show_format" in calls[0]
    assert "-count_frames" in calls[1] and "-count_packets" in calls[1]


class FakeProcess:
    def __init__(self, stdout: bytes = b"{}", stderr: bytes = b"", *, timeout: bool = False) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = 0
        self.timeout = timeout
        self.killed = False
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class BlockingPipe:
    def __init__(self) -> None:
        self.read_started = threading.Event()
        self.close_requested = threading.Event()
        self.reader_exited = threading.Event()
        self.was_closed = False

    def read(self, _size: int) -> bytes:
        self.read_started.set()
        try:
            if not self.close_requested.wait(timeout=5.0):
                raise AssertionError("reader was not closed")
            raise ValueError("I/O operation on closed pipe")
        finally:
            self.reader_exited.set()

    def close(self) -> None:
        self.was_closed = True
        self.close_requested.set()


class BlockingPipeProcess(FakeProcess):
    def __init__(self, *, timeout: bool) -> None:
        super().__init__(timeout=timeout)
        self.stdout = BlockingPipe()
        self.stderr = BlockingPipe()

    def wait(self, timeout: float | None = None) -> int:
        assert self.stdout.read_started.wait(timeout=1.0)
        assert self.stderr.read_started.wait(timeout=1.0)
        return super().wait(timeout)


class UnreapableProcess(BlockingPipeProcess):
    def __init__(self) -> None:
        super().__init__(timeout=True)
        self.allow_reap = threading.Event()
        self.returncode = None

    def wait(self, timeout: float | None = None) -> int:
        assert self.stdout.read_started.wait(timeout=1.0)
        assert self.stderr.read_started.wait(timeout=1.0)
        self.wait_calls.append(timeout)
        if not self.killed:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        if timeout is None:
            self.allow_reap.wait(timeout=0.5)
            self.returncode = -9
            return self.returncode
        if not self.allow_reap.wait(timeout=timeout):
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        self.returncode = -9
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class NonCooperativePipe:
    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.read_started = threading.Event()
        self.reader_exited = threading.Event()
        self.was_closed = False

    def read(self, _size: int) -> bytes:
        self.read_started.set()
        try:
            self.release.wait(timeout=0.5)
            return b""
        finally:
            self.reader_exited.set()

    def close(self) -> None:
        self.was_closed = True


class NonCooperativeReaderProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__(timeout=True)
        self.release_readers = threading.Event()
        self.stdout = NonCooperativePipe(self.release_readers)
        self.stderr = NonCooperativePipe(self.release_readers)


class BlockingClosePipe(io.BytesIO):
    def __init__(self, content: bytes, release_close: threading.Event) -> None:
        super().__init__(content)
        self.release_close = release_close
        self.close_started = threading.Event()
        self.close_finished = threading.Event()

    def close(self) -> None:
        self.close_started.set()
        self.release_close.wait(timeout=0.4)
        try:
            super().close()
        finally:
            self.close_finished.set()


class BlockingCloseProcess(FakeProcess):
    def __init__(self, stdout: bytes, stderr: bytes, *, timeout: bool) -> None:
        super().__init__(timeout=timeout)
        self.release_closes = threading.Event()
        self.stdout = BlockingClosePipe(stdout, self.release_closes)
        self.stderr = BlockingClosePipe(stderr, self.release_closes)


def test_ffprobe_is_spawned_with_argv_devnull_pipes_and_shell_false() -> None:
    captured: dict = {}
    process = FakeProcess(stdout=b'{"ok": true}')

    def factory(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return process

    result = run_ffprobe_bounded(
        ["ffprobe", "-protocol_whitelist", "file", "safe.part"],
        timeout=10,
        output_limit=1024,
        popen_factory=factory,
    )

    assert result == b'{"ok": true}'
    assert isinstance(captured["argv"], list)
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.PIPE
    assert captured["stderr"] is subprocess.PIPE


def test_ffprobe_timeout_explicitly_kills_and_waits_for_child() -> None:
    process = FakeProcess(timeout=True)

    with pytest.raises(MediaProbeTimeoutError) as captured:
        run_ffprobe_bounded(
            ["ffprobe", "safe.part"],
            timeout=0.01,
            output_limit=1024,
            popen_factory=lambda *_args, **_kwargs: process,
        )

    assert process.killed is True
    assert len(process.wait_calls) == 2
    assert captured.value.failure_code is ArtifactImportFailureCode.PROBE_TIMEOUT


def test_ffprobe_timeout_closes_pipes_and_joins_blocked_readers() -> None:
    process = BlockingPipeProcess(timeout=True)

    with pytest.raises(MediaProbeTimeoutError):
        run_ffprobe_bounded(
            ["ffprobe", "safe.part"],
            timeout=0.01,
            output_limit=1024,
            popen_factory=lambda *_args, **_kwargs: process,
        )

    for pipe in (process.stdout, process.stderr):
        assert pipe.was_closed is True
        assert pipe.reader_exited.is_set()
    assert process.killed is True
    assert len(process.wait_calls) == 2


def test_ffprobe_normal_completion_with_stalled_reader_closes_and_joins() -> None:
    process = BlockingPipeProcess(timeout=False)

    with pytest.raises(MediaProbeTimeoutError):
        run_ffprobe_bounded(
            ["ffprobe", "safe.part"],
            timeout=10,
            output_limit=1024,
            popen_factory=lambda *_args, **_kwargs: process,
        )

    for pipe in (process.stdout, process.stderr):
        assert pipe.was_closed is True
        assert pipe.reader_exited.is_set()
    assert process.killed is True
    assert len(process.wait_calls) == 2


def test_ffprobe_timeout_never_uses_an_unbounded_reap_wait() -> None:
    process = UnreapableProcess()
    started_at = time.monotonic()

    try:
        with pytest.raises(MediaProbeTimeoutError):
            run_ffprobe_bounded(
                ["ffprobe", "safe.part"],
                timeout=0.01,
                output_limit=1024,
                popen_factory=lambda *_args, **_kwargs: process,
            )
    finally:
        process.allow_reap.set()
        process.stdout.close()
        process.stderr.close()

    assert time.monotonic() - started_at < 0.25
    assert len(process.wait_calls) == 2
    assert all(timeout is not None for timeout in process.wait_calls)


def test_ffprobe_timeout_never_unboundedly_joins_a_reader_that_ignores_close() -> None:
    process = NonCooperativeReaderProcess()
    started_at = time.monotonic()

    try:
        with pytest.raises(MediaProbeTimeoutError):
            run_ffprobe_bounded(
                ["ffprobe", "safe.part"],
                timeout=0.01,
                output_limit=1024,
                popen_factory=lambda *_args, **_kwargs: process,
            )
    finally:
        process.release_readers.set()
        for pipe in (process.stdout, process.stderr):
            pipe.reader_exited.wait(timeout=1.0)

    assert time.monotonic() - started_at < 0.25
    assert process.stdout.was_closed is True
    assert process.stderr.was_closed is True


def test_ffprobe_timeout_never_blocks_on_pipe_close() -> None:
    process = BlockingCloseProcess(b"{}", b"", timeout=True)
    started_at = time.monotonic()

    try:
        with pytest.raises(MediaProbeTimeoutError):
            run_ffprobe_bounded(
                ["ffprobe", "safe.part"],
                timeout=0.01,
                output_limit=1024,
                popen_factory=lambda *_args, **_kwargs: process,
            )
        elapsed = time.monotonic() - started_at
    finally:
        process.release_closes.set()
        for pipe in (process.stdout, process.stderr):
            assert pipe.close_finished.wait(timeout=1.0)

    assert elapsed < 0.25
    assert process.stdout.close_started.is_set()
    assert process.stderr.close_started.is_set()


def test_ffprobe_output_overflow_beats_incomplete_blocking_pipe_close() -> None:
    process = BlockingCloseProcess(b"x" * 1025, b"", timeout=False)
    started_at = time.monotonic()

    try:
        with pytest.raises(MediaProbeOutputLimitError):
            run_ffprobe_bounded(
                ["ffprobe", "safe.part"],
                timeout=0.01,
                output_limit=1024,
                popen_factory=lambda *_args, **_kwargs: process,
            )
        elapsed = time.monotonic() - started_at
    finally:
        process.release_closes.set()
        for pipe in (process.stdout, process.stderr):
            assert pipe.close_finished.wait(timeout=1.0)

    assert elapsed < 0.25
    assert process.killed is True


def test_ffprobe_combined_output_is_bounded_and_overflow_kills_child() -> None:
    process = FakeProcess(stdout=b"x" * 700, stderr=b"y" * 700)

    with pytest.raises(MediaProbeOutputLimitError) as captured:
        run_ffprobe_bounded(
            ["ffprobe", "safe.part"],
            timeout=10,
            output_limit=1024,
            popen_factory=lambda *_args, **_kwargs: process,
        )

    assert process.killed is True
    assert captured.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA


def test_missing_ffprobe_is_actionable_and_does_not_break_image_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(probe_module.shutil, "which", lambda _name: None)
    video, video_root = staged_path(tmp_path / "video", fixture_bytes("tiny_mp4"))
    image, image_root = staged_path(tmp_path / "image", fixture_bytes("tiny_png"))

    with pytest.raises(MediaProbeUnavailableError, match="ffprobe") as captured:
        probe_video(video, staging_root=video_root)

    assert captured.value.failure_code is ArtifactImportFailureCode.PROBE_UNAVAILABLE
    assert probe_image(image, staging_root=image_root).media_type == "image/png"


def test_explicit_missing_ffprobe_path_is_rejected_at_video_probe_time(tmp_path: Path) -> None:
    missing = tmp_path / "missing-ffprobe.exe"

    with pytest.raises(MediaProbeUnavailableError, match="configured"):
        resolve_ffprobe(missing)


def test_random_or_non_webm_ebml_container_is_rejected_before_subprocess(tmp_path: Path) -> None:
    path, root = staged_path(tmp_path, b"\x1aE\xdf\xa3matroska-not-webm")
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"{}"

    with pytest.raises(UnsupportedMediaError, match="container"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)

    assert called is False


def test_matroska_header_cannot_smuggle_fake_webm_doctype_bytes(tmp_path: Path) -> None:
    fake_marker_value = b"\x42\x86\x87\x42\x82\x84webm"
    actual_doctype = b"\x42\x82\x88matroska"
    ebml_header = b"\x1aE\xdf\xa3" + bytes([0x80 + len(fake_marker_value + actual_doctype)])
    path, root = staged_path(tmp_path, ebml_header + fake_marker_value + actual_doctype)
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"{}"

    with pytest.raises(CorruptMediaError, match="metadata"):
        probe_video(path, staging_root=root, ffprobe_path=installed_ffprobe(), runner=runner)

    assert called is True
