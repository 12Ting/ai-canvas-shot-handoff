"""Strict, local-only media validation."""

from ai_canvas.domain.media.probe import (
    CorruptMediaError,
    MediaProbeError,
    MediaProbeOutputLimitError,
    MediaProbeResult,
    MediaProbeTimeoutError,
    MediaProbeUnavailableError,
    MediaStorageIOError,
    UnsupportedMediaError,
    UnsafeMediaPathError,
    probe_image,
    probe_video,
    resolve_ffprobe,
)

__all__ = [
    "CorruptMediaError",
    "MediaProbeError",
    "MediaProbeOutputLimitError",
    "MediaProbeResult",
    "MediaProbeTimeoutError",
    "MediaProbeUnavailableError",
    "MediaStorageIOError",
    "UnsupportedMediaError",
    "UnsafeMediaPathError",
    "probe_image",
    "probe_video",
    "resolve_ffprobe",
]
