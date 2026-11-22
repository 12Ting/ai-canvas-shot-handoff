import hashlib
import shutil
import subprocess

import pytest
from PIL import Image

from ai_canvas.application.handoff_frames import boundary_frame
from ai_canvas.config import Settings


@pytest.mark.parametrize("colors", [("red",), ("red", "green", "blue")])
def test_frame_cache_preserves_display_boundaries_on_single_and_variable_rate_videos(tmp_path, colors):
    executable = shutil.which("ffmpeg")
    if not executable:
        pytest.skip("requires local ffmpeg")
    lines = []
    for index, color in enumerate(colors):
        name = f"{index}.png"
        Image.new("RGB", (16, 16), color).save(tmp_path / name)
        lines.extend([f"file '{name}'", f"duration {0.12 if index == 0 else 0.48}"])
    listing = tmp_path / "frames.txt"
    listing.write_text("\n".join(lines) + "\n", encoding="ascii")
    source = tmp_path / "vfr.mp4"
    subprocess.run([executable, "-v", "error", "-f", "concat", "-i", str(listing),
                    "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                   check=True, timeout=15, capture_output=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    settings = Settings(data_dir=tmp_path / "app")
    for edge, dominant in (("first", 0), ("last", 0 if len(colors) == 1 else 2)):
        frame = boundary_frame(settings, source, digest, edge)
        with Image.open(frame) as image:
            rgb = image.convert("RGB").getpixel((8, 8))
        assert rgb[dominant] > 190 and sum(rgb) - rgb[dominant] < 40
        modified = frame.stat().st_mtime_ns
        assert boundary_frame(settings, source, digest, edge) == frame
        assert frame.stat().st_mtime_ns == modified
