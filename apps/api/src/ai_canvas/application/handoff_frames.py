"""Cache the first and last display frames of a verified local video version."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore

_decoders = BoundedSemaphore(2)


def boundary_frame(settings, source: Path, digest: str, edge: str) -> Path:
    cache = settings.data_dir / "handoff-frames" / "jpeg-640-v1"
    destination = cache / (digest + "-" + edge + ".jpg")
    with _decoders:
        if destination.is_file():
            return destination
        executable = settings.ffmpeg_path or shutil.which("ffmpeg")
        if not executable:
            raise ValueError("提取首末帧需要 FFmpeg，可先直接播放片段")
        cache.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(suffix=".jpg", dir=cache)
        os.close(descriptor)
        temp = Path(name)
        try:
            command = [
                str(executable),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file",
                "-threads",
                "2",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                "scale='min(640,iw)':-2",
                "-q:v",
                "4",
                "-fps_mode",
                "passthrough",
            ]
            if edge == "first":
                command.extend(["-frames:v", "1"])
            command.extend(["-update", "1", str(temp)])
            try:
                subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("无法读取这个版本的画面，可直接播放片段") from exc
            if temp.stat().st_size == 0:
                raise ValueError("视频没有可显示的帧")
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    return destination
