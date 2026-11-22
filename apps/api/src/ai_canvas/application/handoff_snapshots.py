"""Project-root containment and immutable snapshots for directory-based handoffs."""

from __future__ import annotations

import json
import math
import os
import stat
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from ai_canvas.config import Settings
from ai_canvas.domain.media.probe import probe_image, probe_video, resolve_ffprobe, run_ffprobe_bounded
from ai_canvas.infrastructure.artifacts.store import ArtifactStore
from ai_canvas.infrastructure.media.files import SharedFileLease


def checked_path(root: Path, relative: str = "") -> Path:
    """Validate existing components, including dangling links. New leaves may be absent."""
    if relative and (PureWindowsPath(relative).drive or "\\" in relative or relative.startswith("/")
                     or any(p in ("", ".", "..") or ":" in p or p.endswith((" ", "."))
                            or PureWindowsPath(p).is_reserved() for p in relative.split("/"))):
        raise ValueError("目录路径必须是项目内的相对路径")
    target = root.joinpath(*relative.split("/")) if relative else root
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("需要确定的项目根目录")
    for entry in (*reversed(target.parents), target):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("项目路径包含符号链接或连接点")
    return target


def signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def source_lease(path: Path, root: Path, info: os.stat_result) -> SharedFileLease:
    return SharedFileLease.acquire(
        path, trusted_root=root, expected_size=info.st_size,
        expected_identity=f"{info.st_dev}:{info.st_ino}",
        expected_modified_at=datetime.fromtimestamp(info.st_mtime, timezone.utc),
    )


def read_small(root: Path, path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("文本或项目清单大小超出范围")
    if info.st_size == 0:
        return b""
    with closing(source_lease(path, root, info)) as lease:
        data = b"".join(lease.read_range(start=0, length=info.st_size, chunk_bytes=65536))
    if signature(path.stat()) != signature(info):
        raise ValueError("读取时文件发生变化")
    return data


def _audio(path: Path, expected: str, settings: Settings) -> tuple[str, dict]:
    # Force the local demuxer so a mislabeled playlist cannot load sibling files.
    executable = resolve_ffprobe(settings.ffprobe_path)
    raw = run_ffprobe_bounded([
        executable, "-v", "error", "-protocol_whitelist", "file", "-f", expected,
        "-count_frames", "-show_entries",
        "stream=codec_type,codec_name,nb_read_frames:format=format_name,duration",
        "-of", "json", str(path),
    ], timeout=settings.artifact_probe_full_scan_timeout_seconds, output_limit=65536)
    obj = json.loads(raw)
    streams = obj.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "audio":
        raise ValueError("音频文件需要一条可播放的音轨")
    codec = streams[0].get("codec_name", "")
    if int(streams[0].get("nb_read_frames", 0)) < 1 or (
        expected == "mp3" and codec != "mp3"
    ) or (expected == "wav" and not codec.startswith("pcm_")):
        raise ValueError("仅支持 MP3 和 PCM WAV 配音")
    duration = float(obj["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("音频时长无效")
    return ("audio/mpeg" if expected == "mp3" else "audio/wav"), {"duration_seconds": duration}


class SnapshotStore:
    def __init__(self, data_dir: Path, settings: Settings):
        self.settings = settings
        # Foundation's GC only tracks its own SQLite artifact references.
        self.store = ArtifactStore(data_dir / "handoff-media", max_bytes=settings.media_single_file_max_bytes)

    def blob_path(self, digest: str) -> Path:
        path = self.store.blob_path(digest)
        checked_path(self.store.data_dir, path.relative_to(self.store.data_dir).as_posix())
        if not path.is_file():
            raise FileNotFoundError("缺少固定的素材版本")
        return path

    def capture(self, root: Path, path: Path, kind: str, info: os.stat_result) -> dict:
        settings = self.settings
        with closing(source_lease(path, root, info)) as lease:
            staged = self.store.stage_chunks(uuid4(), lease.read_range(
                start=0, length=info.st_size, chunk_bytes=self.store.chunk_size,
            ), declared_size=info.st_size)
        try:
            if signature(path.stat()) != signature(info):
                raise ValueError("文件仍在写入")
            if kind == "audio":
                mime, metadata = _audio(staged.path, path.suffix[1:].lower(), settings)
            else:
                if kind == "image":
                    probe = probe_image(staged.path, staging_root=self.store.staging_root,
                                        max_pixels=settings.artifact_image_max_pixels,
                                        max_side=settings.artifact_image_max_side)
                else:
                    probe = probe_video(
                        staged.path, staging_root=self.store.staging_root,
                        ffprobe_path=settings.ffprobe_path,
                        max_side=settings.artifact_video_max_side,
                        max_duration=settings.artifact_video_max_duration_seconds,
                        max_fps=settings.artifact_video_max_fps,
                        metadata_timeout=settings.artifact_probe_metadata_timeout_seconds,
                        full_scan_timeout=settings.artifact_probe_full_scan_timeout_seconds,
                        output_limit=settings.artifact_probe_output_limit_bytes,
                    )
                mime, metadata = probe.media_type, probe.metadata.model_dump(mode="json")
            self.store.promote(staged)
        finally:
            if staged.path.exists():
                self.store.discard(staged)
        return {"sha256": staged.sha256, "bytes": staged.byte_size, "byte_size": staged.byte_size, "kind": kind,
                "mime": mime, "metadata": metadata}
