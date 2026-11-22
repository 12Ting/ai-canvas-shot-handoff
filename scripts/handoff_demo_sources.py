"""Generate original paper-boat footage and its reference material."""

from dataclasses import dataclass, field
import math
from pathlib import Path
import shutil
import struct
import subprocess
import wave

from PIL import Image, ImageDraw


@dataclass
class Take:
    label: str
    reference: Path
    prompt: str
    config: str
    video: Path


@dataclass
class Shot:
    title: str
    takes: list[Take]
    dialogue: str = ""
    sounds: list[Path] = field(default_factory=list)


def _stage_frame(camera, direction, i):
    # Three deliberately different compositions; the middle shot has a reversal to spot.
    width, height = 192, 108
    frame = Image.new("RGB", (width, height), (26, 48 + camera * 9, 59))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 66, width, 108), fill=(40, 106, 113))
    draw.ellipse((136, 10, 154, 28), fill=(245, 197, 121))
    x = 20 + (i if direction == 1 else 23 - i) * 4
    draw.polygon([(x, 63), (x + 48, 63), (x + 37, 76), (x + 12, 76)], fill=(226, 103, 80))
    draw.polygon([(x + 9, 61), (x + 27, 38), (x + 29, 61)], fill=(246, 226, 187))
    return frame


def original(work: Path) -> list[Shot]:
    converter = shutil.which("ffmpeg")
    if converter is None:
        raise RuntimeError("制作可分发样例需要 FFmpeg")
    work.mkdir(parents=True)
    sound = work / "提示音.wav"
    with wave.open(str(sound), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", ""))
        samples = [int(3500 * math.sin(2 * math.pi * 440 * i / 16000) * math.exp(-i / 2600))
                   for i in range(16000)]
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    result = []
    for camera, title in enumerate(("纸船入画", "经过灯下", "离开水面"), 1):
        takes = []
        for direction in ((-1, 1) if camera == 2 else (1,)):
            label = "向右" if direction == 1 else "向左"
            stem = f"SH{camera:02d}-{label}"
            video = work / (stem + ".mp4")
            reference = work / (stem + ".png")
            frames = [_stage_frame(camera, direction, i) for i in range(24)]
            frames[0].save(reference)
            subprocess.run([converter, "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", "192x108", "-framerate", "12", "-i", "-", "-an", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)],
                           input=b"".join(f.tobytes() for f in frames), check=True, timeout=60)
            takes.append(Take(label, reference, f"固定机位，纸船{label}划过。", "程序绘制，12 fps，2 秒", video))
        result.append(Shot(title, takes, "灯亮了。" if camera == 2 else "", [sound] if camera == 2 else []))
    return result
