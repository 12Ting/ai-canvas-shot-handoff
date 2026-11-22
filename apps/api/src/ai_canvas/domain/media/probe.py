from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from ai_canvas.contracts.artifact import (
    ArtifactImportFailureCode,
    ImageArtifactMetadata,
    VideoArtifactMetadata,
)

DEFAULT_METADATA_TIMEOUT_SECONDS = 10.0
DEFAULT_FULL_SCAN_TIMEOUT_SECONDS = 120.0
DEFAULT_OUTPUT_LIMIT_BYTES = 256 * 1024
DEFAULT_IMAGE_MAX_PIXELS = 50_000_000
DEFAULT_IMAGE_MAX_SIDE = 16_384
DEFAULT_VIDEO_MAX_DURATION_SECONDS = 600.0
DEFAULT_VIDEO_MAX_SIDE = 4_096
DEFAULT_VIDEO_MAX_FPS = 120.0
FFPROBE_CLEANUP_GRACE_SECONDS = 0.05
_ALLOWED_PIL_FORMATS = ("JPEG", "PNG", "WEBP")
_IMAGE_MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_STAGING_NAME_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.part\Z"
)
_MP4_BRANDS = frozenset(
    {
        b"avc1",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"isom",
        b"mp41",
        b"mp42",
    }
)
_MOV_BRANDS = frozenset({b"qt  "})


class MediaProbeError(ValueError):
    failure_code: ArtifactImportFailureCode

    def __init__(self, message: str, failure_code: ArtifactImportFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(message)


class MediaStorageIOError(MediaProbeError):
    def __init__(self) -> None:
        super().__init__(
            "media storage is temporarily unavailable",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


class CorruptMediaError(MediaProbeError):
    def __init__(self, message: str = "media content is corrupt") -> None:
        super().__init__(message, ArtifactImportFailureCode.CORRUPT_MEDIA)


class UnsupportedMediaError(MediaProbeError):
    def __init__(self, message: str = "media format is unsupported") -> None:
        super().__init__(message, ArtifactImportFailureCode.UNSUPPORTED_MEDIA)


class MediaProbeTimeoutError(MediaProbeError):
    def __init__(self) -> None:
        super().__init__("media probe timed out", ArtifactImportFailureCode.PROBE_TIMEOUT)


class MediaProbeUnavailableError(MediaProbeError):
    def __init__(self, message: str = "ffprobe is unavailable; install FFmpeg or configure ffprobe_path") -> None:
        super().__init__(message, ArtifactImportFailureCode.PROBE_UNAVAILABLE)


class MediaProbeOutputLimitError(CorruptMediaError):
    def __init__(self) -> None:
        super().__init__("ffprobe output exceeded the configured safety limit")


class UnsafeMediaPathError(CorruptMediaError):
    def __init__(self) -> None:
        super().__init__("media probe path is not a server-generated staging file")


@dataclass(frozen=True, slots=True)
class MediaProbeResult:
    kind: Literal["image", "video"]
    media_type: str
    metadata: ImageArtifactMetadata | VideoArtifactMetadata


class _Process(Protocol):
    stdout: Any
    stderr: Any
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class _Runner(Protocol):
    def __call__(self, argv: list[str], *, timeout: float, output_limit: int) -> bytes: ...


def probe_image(
    path: Path,
    *,
    staging_root: Path | None = None,
    trusted_root: Path | None = None,
    max_pixels: int = DEFAULT_IMAGE_MAX_PIXELS,
    max_side: int = DEFAULT_IMAGE_MAX_SIDE,
) -> MediaProbeResult:
    staged_path = _validated_probe_path(path, staging_root=staging_root, trusted_root=trusted_root)
    if max_pixels < 1 or max_side < 1:
        raise ValueError("image limits must be positive")
    expected_format = _sniff_image_format(staged_path)
    if expected_format is None:
        raise UnsupportedMediaError("image container is unsupported")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(staged_path, formats=_ALLOWED_PIL_FORMATS) as image:
                format_name = image.format
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
                _validate_image_shape(width, height, max_pixels=max_pixels, max_side=max_side)
                if frames != 1:
                    raise UnsupportedMediaError("animated images are unsupported")
                if format_name != expected_format:
                    raise CorruptMediaError("image signature and decoder disagree")
                image.verify()
            with Image.open(staged_path, formats=_ALLOWED_PIL_FORMATS) as decoded:
                if decoded.format != format_name or decoded.size != (width, height):
                    raise CorruptMediaError("image metadata changed between verification passes")
                if getattr(decoded, "n_frames", 1) != 1:
                    raise UnsupportedMediaError("animated images are unsupported")
                decoded.load()
    except (UnsupportedMediaError, CorruptMediaError):
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise UnsupportedMediaError("image pixel count exceeds its safety limit") from error
    except (UnidentifiedImageError, SyntaxError, ValueError) as error:
        raise CorruptMediaError() from error
    except OSError as error:
        if error.errno is not None:
            raise MediaStorageIOError from error
        raise CorruptMediaError() from error
    if format_name not in _IMAGE_MEDIA_TYPES:
        raise UnsupportedMediaError()
    return MediaProbeResult(
        kind="image",
        media_type=_IMAGE_MEDIA_TYPES[format_name],
        metadata=ImageArtifactMetadata(width=width, height=height),
    )


def probe_video(
    path: Path,
    *,
    staging_root: Path | None = None,
    trusted_root: Path | None = None,
    ffprobe_path: Path | None = None,
    metadata_timeout: float = DEFAULT_METADATA_TIMEOUT_SECONDS,
    full_scan_timeout: float = DEFAULT_FULL_SCAN_TIMEOUT_SECONDS,
    output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    max_duration: float = DEFAULT_VIDEO_MAX_DURATION_SECONDS,
    max_side: int = DEFAULT_VIDEO_MAX_SIDE,
    max_fps: float = DEFAULT_VIDEO_MAX_FPS,
    runner: _Runner | None = None,
) -> MediaProbeResult:
    staged_path = _validated_probe_path(path, staging_root=staging_root, trusted_root=trusted_root)
    if min(metadata_timeout, full_scan_timeout, max_duration, max_fps) <= 0:
        raise ValueError("video time and frame-rate limits must be positive")
    if output_limit < 1 or max_side < 1:
        raise ValueError("video byte and dimension limits must be positive")
    container = _sniff_video_container(staged_path)
    executable = resolve_ffprobe(ffprobe_path)
    run = run_ffprobe_bounded if runner is None else runner
    metadata_payload = _decode_probe_json(
        run(_metadata_argv(executable, staged_path), timeout=metadata_timeout, output_limit=output_limit)
    )
    parsed = _validate_video_metadata(
        metadata_payload,
        container=container,
        max_duration=max_duration,
        max_side=max_side,
        max_fps=max_fps,
    )
    scan_payload = _decode_probe_json(
        run(_scan_argv(executable, staged_path), timeout=full_scan_timeout, output_limit=output_limit)
    )
    _validate_full_scan(scan_payload, expected_stream_indexes=parsed.stream_indexes)
    return MediaProbeResult(
        kind="video",
        media_type={
            "mp4": "video/mp4",
            "mov": "video/quicktime",
            "webm": "video/webm",
            "mkv": "video/x-matroska",
        }[container],
        metadata=VideoArtifactMetadata(
            width=parsed.width,
            height=parsed.height,
            duration_seconds=parsed.duration,
            frames_per_second=parsed.fps,
        ),
    )


def resolve_ffprobe(configured_path: Path | None) -> str:
    if configured_path is not None:
        candidate = Path(configured_path)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise MediaProbeUnavailableError(
                "configured ffprobe_path does not point to an executable file"
            ) from error
        if not resolved.is_file():
            raise MediaProbeUnavailableError(
                "configured ffprobe_path does not point to an executable file"
            )
        return str(resolved)
    discovered = shutil.which("ffprobe")
    if discovered is None:
        raise MediaProbeUnavailableError()
    return discovered


def run_ffprobe_bounded(
    argv: list[str],
    *,
    timeout: float,
    output_limit: int,
    popen_factory: Callable[..., _Process] = subprocess.Popen,
) -> bytes:
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise TypeError("ffprobe must be invoked with a non-empty string argument list")
    if timeout <= 0 or output_limit < 1:
        raise ValueError("ffprobe limits must be positive")
    run_deadline = time.monotonic() + timeout
    try:
        process = popen_factory(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
    except (FileNotFoundError, PermissionError, OSError) as error:
        raise MediaProbeUnavailableError() from error
    if process.stdout is None or process.stderr is None:
        cleanup_deadline = time.monotonic() + FFPROBE_CLEANUP_GRACE_SECONDS
        _kill_process_best_effort(process)
        close_workers = _start_process_pipe_closers(process)
        _wait_process_until(process, cleanup_deadline)
        _join_threads_until(close_workers, cleanup_deadline)
        raise MediaProbeUnavailableError("ffprobe output pipes could not be created")

    stdout = bytearray()
    stderr = bytearray()
    state_lock = threading.Lock()
    overflow = threading.Event()
    closing_pipes = threading.Event()
    reader_errors: list[OSError | ValueError] = []

    def read_pipe(stream, destination: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                should_kill = False
                with state_lock:
                    combined_size = len(stdout) + len(stderr)
                    remaining = output_limit - combined_size
                    if len(chunk) > remaining:
                        if remaining > 0:
                            destination.extend(chunk[:remaining])
                        if not overflow.is_set():
                            overflow.set()
                            should_kill = True
                    else:
                        destination.extend(chunk)
                if should_kill:
                    try:
                        process.kill()
                    except OSError:
                        pass
        except (OSError, ValueError) as error:
            if not closing_pipes.is_set():
                with state_lock:
                    reader_errors.append(error)

    readers = [
        threading.Thread(target=read_pipe, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=read_pipe, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    timeout_error: subprocess.TimeoutExpired | None = None
    monitor_error: OSError | None = None
    cleanup_incomplete = False
    try:
        try:
            process.wait(timeout=_seconds_until(run_deadline))
        except subprocess.TimeoutExpired as error:
            timed_out = True
            timeout_error = error
        except OSError as error:
            monitor_error = error
        if not timed_out and monitor_error is None:
            drain_deadline = min(
                run_deadline,
                time.monotonic() + FFPROBE_CLEANUP_GRACE_SECONDS,
            )
            if not _join_threads_until(readers, drain_deadline):
                timed_out = True
    finally:
        cleanup_deadline = time.monotonic() + FFPROBE_CLEANUP_GRACE_SECONDS
        readers_alive = any(reader.is_alive() for reader in readers)
        needs_termination = (
            timed_out
            or overflow.is_set()
            or monitor_error is not None
            or readers_alive
            or process.returncode is None
        )
        process_reaped = True
        if needs_termination:
            _kill_process_best_effort(process)
        closing_pipes.set()
        close_workers = _start_process_pipe_closers(process)
        if needs_termination:
            process_reaped = _wait_process_until(process, cleanup_deadline)
        pipes_closed = _join_threads_until(close_workers, cleanup_deadline)
        readers_finished = _join_threads_until(readers, cleanup_deadline)
        cleanup_incomplete = not process_reaped or not pipes_closed or not readers_finished

    if overflow.is_set():
        if timeout_error is not None:
            raise MediaProbeOutputLimitError from timeout_error
        raise MediaProbeOutputLimitError
    if timed_out or cleanup_incomplete:
        if timeout_error is not None:
            raise MediaProbeTimeoutError from timeout_error
        raise MediaProbeTimeoutError
    if monitor_error is not None:
        raise CorruptMediaError("ffprobe process could not be monitored") from monitor_error
    if reader_errors:
        raise CorruptMediaError("ffprobe output could not be read") from reader_errors[0]
    if process.returncode != 0 or stderr:
        raise CorruptMediaError("ffprobe reported a media decoding error")
    return bytes(stdout)


@dataclass(frozen=True, slots=True)
class _ParsedVideo:
    width: int
    height: int
    duration: float
    fps: float
    stream_indexes: frozenset[int]


def _validate_video_metadata(
    payload: dict[str, Any],
    *,
    container: Literal["mp4", "mov", "webm", "mkv"],
    max_duration: float,
    max_side: int,
    max_fps: float,
) -> _ParsedVideo:
    streams = payload.get("streams")
    format_payload = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_payload, dict):
        raise CorruptMediaError("ffprobe metadata is incomplete")
    format_name = format_payload.get("format_name")
    allowed_format_names = {
        "mp4": {"mp4"},
        "mov": {"mov"},
        "webm": {"webm"},
        "mkv": {"matroska"},
    }
    if not isinstance(format_name, str) or not (
        allowed_format_names[container] & set(format_name.split(","))
    ):
        raise UnsupportedMediaError("video container metadata is incompatible")
    typed_streams = [stream for stream in streams if isinstance(stream, dict)]
    if len(typed_streams) != len(streams):
        raise CorruptMediaError("ffprobe stream metadata is malformed")
    video_streams = [stream for stream in typed_streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    if any(stream.get("codec_type") not in {"video", "audio"} for stream in typed_streams):
        raise UnsupportedMediaError("video contains a forbidden stream type")
    if len(video_streams) != 1:
        raise UnsupportedMediaError("video must contain exactly one video stream")
    if len(audio_streams) > 1:
        raise UnsupportedMediaError("video may contain at most one audio stream")
    video = video_streams[0]
    expected_video_codecs = {
        "mp4": {"h264"},
        "mov": {"h264"},
        "webm": {"vp8", "vp9"},
        "mkv": {"h264", "vp8", "vp9"},
    }[container]
    expected_audio_codecs = {
        "mp4": {"aac"},
        "mov": {"aac"},
        "webm": {"opus", "vorbis"},
        "mkv": {"aac", "opus", "vorbis"},
    }[container]
    if video.get("codec_name") not in expected_video_codecs:
        raise UnsupportedMediaError("video codec is incompatible with its container")
    if video.get("pix_fmt") != "yuv420p" or str(video.get("bits_per_raw_sample", "8")) not in {
        "",
        "8",
        "N/A",
    }:
        raise UnsupportedMediaError("video must use 8-bit 4:2:0 pixels")
    if any(audio.get("codec_name") not in expected_audio_codecs for audio in audio_streams):
        raise UnsupportedMediaError("audio codec is incompatible with its container")
    try:
        width = int(video["width"])
        height = int(video["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise CorruptMediaError("video dimensions are missing") from error
    if width < 1 or height < 1 or width > max_side or height > max_side:
        raise UnsupportedMediaError("video dimensions exceed the configured limit")
    duration = _parse_duration(format_payload.get("duration"), video)
    if not math.isfinite(duration) or duration <= 0 or duration > max_duration:
        raise UnsupportedMediaError("video duration exceeds the configured limit")
    fps = _parse_frame_rate(video)
    if not math.isfinite(fps) or fps <= 0 or fps > max_fps:
        raise UnsupportedMediaError("video fps exceeds the configured limit")
    try:
        stream_indexes = frozenset(int(stream["index"]) for stream in typed_streams)
    except (KeyError, TypeError, ValueError) as error:
        raise CorruptMediaError("video stream indexes are malformed") from error
    if len(stream_indexes) != len(typed_streams):
        raise CorruptMediaError("video stream indexes are not unique")
    return _ParsedVideo(width, height, duration, fps, stream_indexes)


def _validate_full_scan(
    payload: dict[str, Any],
    *,
    expected_stream_indexes: frozenset[int],
) -> None:
    streams = payload.get("streams")
    if not isinstance(streams, list) or payload.get("error"):
        raise CorruptMediaError("video full scan failed")
    seen: set[int] = set()
    for stream in streams:
        if not isinstance(stream, dict):
            raise CorruptMediaError("video full scan is malformed")
        try:
            index = int(stream["index"])
            packet_count = int(stream["nb_read_packets"])
        except (KeyError, TypeError, ValueError) as error:
            raise CorruptMediaError("video full scan is incomplete") from error
        if index in seen:
            raise CorruptMediaError("video full scan contains duplicate stream indexes")
        if packet_count < 1:
            raise CorruptMediaError("video full scan found no packets")
        if stream.get("codec_type") == "video" or "nb_read_frames" in stream:
            try:
                frame_count = int(stream["nb_read_frames"])
            except (KeyError, TypeError, ValueError) as error:
                raise CorruptMediaError("video full scan frame count is invalid") from error
            if frame_count < 1:
                raise CorruptMediaError("video full scan found no decoded frames")
        seen.add(index)
    if seen != set(expected_stream_indexes):
        raise CorruptMediaError("video full scan did not cover every stream")


def _metadata_argv(executable: str, path: Path) -> list[str]:
    return [
        executable,
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]


def _scan_argv(executable: str, path: Path) -> list[str]:
    return [
        executable,
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-count_packets",
        "-count_frames",
        "-show_error",
        "-show_entries",
        "stream=index,codec_type,nb_read_packets,nb_read_frames:format=format_name",
        "-of",
        "json",
        str(path),
    ]


def _decode_probe_json(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise CorruptMediaError("ffprobe returned malformed metadata") from error
    if not isinstance(payload, dict) or payload.get("error"):
        raise CorruptMediaError("ffprobe could not read the complete media file")
    return payload


def _sniff_image_format(path: Path) -> str | None:
    try:
        with path.open("rb") as source:
            header = source.read(16)
    except OSError as error:
        raise MediaStorageIOError from error
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    return None


def _sniff_video_container(path: Path) -> Literal["mp4", "mov", "webm", "mkv"]:
    try:
        with path.open("rb") as source:
            header = source.read(4096)
    except OSError as error:
        raise MediaStorageIOError from error
    if len(header) >= 16 and header[4:8] == b"ftyp":
        box_size = int.from_bytes(header[:4], "big")
        if 16 <= box_size <= len(header):
            major_brand = header[8:12]
            brands = {major_brand}
            brands.update(header[offset : offset + 4] for offset in range(16, box_size, 4))
            if major_brand in _MOV_BRANDS:
                return "mov"
            if brands & _MP4_BRANDS:
                return "mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        doctype = _ebml_doctype(header)
        if doctype == b"webm":
            return "webm"
        if doctype == b"matroska":
            return "mkv"
    raise UnsupportedMediaError("video container is unsupported")


def _ebml_doctype(header: bytes) -> bytes | None:
    header_size = _read_ebml_vint(header, 4)
    if header_size is None:
        return None
    payload_size, size_width = header_size
    payload_start = 4 + size_width
    payload_end = payload_start + payload_size
    if payload_end > len(header):
        return None
    payload = header[payload_start:payload_end]
    offset = 0
    while offset < len(payload):
        identifier_width = _ebml_identifier_width(payload, offset)
        if identifier_width is None:
            return None
        identifier = payload[offset : offset + identifier_width]
        size_offset = offset + identifier_width
        value_size = _read_ebml_vint(payload, size_offset)
        if value_size is None:
            return None
        length, size_width = value_size
        value_start = size_offset + size_width
        value_end = value_start + length
        if value_end > len(payload):
            return None
        if identifier == b"\x42\x82":
            return payload[value_start:value_end].lower()
        offset = value_end
    return None


def _ebml_identifier_width(content: bytes, offset: int) -> int | None:
    if offset >= len(content):
        return None
    first = content[offset]
    mask = 0x80
    width = 1
    while width <= 4 and not first & mask:
        mask >>= 1
        width += 1
    if width > 4 or offset + width > len(content):
        return None
    return width


def _read_ebml_vint(content: bytes, offset: int) -> tuple[int, int] | None:
    if offset >= len(content):
        return None
    first = content[offset]
    mask = 0x80
    width = 1
    while width <= 8 and not first & mask:
        mask >>= 1
        width += 1
    if width > 8 or offset + width > len(content):
        return None
    value = first & (mask - 1)
    for byte in content[offset + 1 : offset + width]:
        value = (value << 8) | byte
    return value, width


def _parse_duration(value: Any, video_stream: dict[str, Any]) -> float:
    candidate = value if value not in (None, "N/A", "") else video_stream.get("duration")
    if candidate not in (None, "N/A", ""):
        try:
            return float(candidate)
        except (TypeError, ValueError):
            pass
    tags = video_stream.get("tags")
    if isinstance(tags, dict):
        tagged = tags.get("DURATION") or tags.get("duration")
        if isinstance(tagged, str):
            parts = tagged.split(":")
            if len(parts) == 3:
                try:
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                except ValueError:
                    pass
    raise CorruptMediaError("video duration is missing")


def _parse_frame_rate(video_stream: dict[str, Any]) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video_stream.get(key)
        if not isinstance(raw, str) or raw in {"", "0/0", "N/A"}:
            continue
        try:
            value = float(Fraction(raw))
        except (ValueError, ZeroDivisionError):
            continue
        if value > 0:
            return value
    raise UnsupportedMediaError("video fps is invalid")


def _validate_image_shape(width: int, height: int, *, max_pixels: int, max_side: int) -> None:
    if width < 1 or height < 1 or width > max_side or height > max_side:
        raise UnsupportedMediaError("image dimensions exceed the configured limit")
    if width * height > max_pixels:
        raise UnsupportedMediaError("image pixels exceed the configured limit")


def _validated_probe_path(
    path: Path,
    *,
    staging_root: Path | None,
    trusted_root: Path | None,
) -> Path:
    if (staging_root is None) == (trusted_root is None):
        raise ValueError("exactly one trusted probe root is required")
    if staging_root is not None:
        return _validated_staging_path(path, staging_root)
    assert trusted_root is not None
    return _validated_external_path(path, trusted_root)


def _validated_external_path(path: Path, trusted_root: Path) -> Path:
    supplied = Path(os.path.abspath(os.fspath(path)))
    root = Path(os.path.abspath(os.fspath(trusted_root)))
    try:
        supplied.relative_to(root)
    except ValueError as error:
        raise UnsafeMediaPathError from error
    _require_trusted_probe_directory_chain(root, supplied.parent)
    try:
        metadata = supplied.lstat()
    except OSError as error:
        raise MediaStorageIOError from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or attributes & reparse_flag:
        raise UnsafeMediaPathError
    return supplied


def _validated_staging_path(path: Path, staging_root: Path) -> Path:
    supplied = Path(os.path.abspath(os.fspath(path)))
    expected_root = Path(os.path.abspath(os.fspath(staging_root)))
    if (
        expected_root.name != ".staging"
        or expected_root.parent.name != "artifacts"
        or supplied.parent != expected_root
        or _STAGING_NAME_RE.fullmatch(supplied.name) is None
    ):
        raise UnsafeMediaPathError
    _require_trusted_probe_directory_chain(expected_root.parent.parent, expected_root)
    try:
        parsed_id = UUID(supplied.name.removesuffix(".part"))
    except ValueError as error:
        raise UnsafeMediaPathError from error
    try:
        metadata = supplied.lstat()
    except OSError as error:
        raise MediaStorageIOError from error
    if str(parsed_id) + ".part" != supplied.name:
        raise UnsafeMediaPathError
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISREG(metadata.st_mode) or attributes & reparse_flag:
        raise UnsafeMediaPathError
    return supplied


def _require_trusted_probe_directory_chain(anchor: Path, target: Path) -> None:
    try:
        relative = target.relative_to(anchor)
    except ValueError as error:
        raise UnsafeMediaPathError from error
    directories = [anchor]
    current = anchor
    for part in relative.parts:
        current /= part
        directories.append(current)
    for directory in directories:
        try:
            metadata = directory.lstat()
        except OSError as error:
            raise MediaStorageIOError from error
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or attributes & reparse_flag
        ):
            raise UnsafeMediaPathError


def _seconds_until(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _kill_process_best_effort(process: _Process) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _wait_process_until(process: _Process, deadline: float) -> bool:
    try:
        process.wait(timeout=_seconds_until(deadline))
    except (subprocess.TimeoutExpired, OSError):
        return False
    return True


def _start_process_pipe_closers(process: _Process) -> list[threading.Thread]:
    def close_pipe(stream: Any) -> None:
        try:
            stream.close()
        except (OSError, ValueError):
            pass

    workers = [
        threading.Thread(target=close_pipe, args=(stream,), daemon=True)
        for stream in (process.stdout, process.stderr)
        if stream is not None
    ]
    for worker in workers:
        worker.start()
    return workers


def _join_threads_until(threads: list[threading.Thread], deadline: float) -> bool:
    for thread in threads:
        thread.join(timeout=_seconds_until(deadline))
    return not any(thread.is_alive() for thread in threads)
