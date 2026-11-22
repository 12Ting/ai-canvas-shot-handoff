import io
import json
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from ai_canvas.application.handoff_files import HandoffFiles, INPUTS, MARKER, REFS, RESULTS
from ai_canvas.application.handoff_snapshots import checked_path
from ai_canvas.config import Settings


class Picker:
    def __init__(self, parent):
        self.parent = parent

    def choose_directory(self):
        return self.parent


def png(color):
    out = io.BytesIO()
    Image.new("RGB", (8, 8), color=color).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def project(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    files = HandoffFiles(tmp_path / "appdata", Picker(parent), settle_seconds=0)
    project_id = str(uuid4())
    root = files.bind(project_id, "秋天")
    group = {"id": str(uuid4()), "shot_id": str(uuid4()), "title": "甲", "path": "E01/S1/G1",
             "conditions": [], "references": [], "bindings": [], "candidates": []}
    state = {"project_id": project_id, "name": "秋天", "groups": [group], "assets": []}
    files.ensure_group(root, group["path"])
    return files, root, state, group


def test_picker_binds_only_matching_project_and_preserves_existing_files(project):
    files, root, state, group = project
    assert files.bind(state["project_id"], "", existing_root=root) == root
    with pytest.raises(ValueError):
        files.bind(str(uuid4()), "", existing_root=root)
    data = root / group["path"] / INPUTS[0]
    data.write_text("保留", encoding="utf-8")
    files.ensure_group(root, group["path"])
    assert data.read_text(encoding="utf-8") == "保留"
    for invalid in ("../other", "test/../../other", "C:/other", "NUL", "test:stream", "//server/share"):
        with pytest.raises(ValueError):
            checked_path(root, invalid)


def test_launch_parent_creates_project_in_explicit_folder(tmp_path):
    parent = tmp_path / "剪辑 工程"
    parent.mkdir()
    data = tmp_path / "app"
    files = HandoffFiles(data, Picker(tmp_path), settings=Settings(data_dir=data, handoff_parent=parent))
    root = files.bind(str(uuid4()), "纸船")
    assert root == parent / "纸船"
    assert (root / MARKER).is_file()
    assert not (root / "共享素材").exists()


def test_same_conditions_multiple_takes_partial_download_and_immutable_history(project):
    files, root, state, group = project
    folder = root / group["path"]
    (folder / INPUTS[0]).write_text("慢动作", encoding="utf-8-sig")
    (folder / INPUTS[1]).write_text("4 s", encoding="utf-8")
    for i, color in enumerate(("red", "blue", "green", "purple")):
        (folder / RESULTS / f"{i}.png").write_bytes(png(color))
    (folder / RESULTS / "unready.crdownload").write_bytes(b"partial")
    files.scan(root, state)
    assert len(group["conditions"]) == 1
    assert not group["candidates"]
    assert files.scan(root, state)
    assert len(group["candidates"]) == 4
    assert {c["condition_id"] for c in group["candidates"]} == {group["conditions"][0]["id"]}
    assert not files.scan(root, state)
    first = group["candidates"][0]
    old_bytes = files.blob_path(first["sha256"]).read_bytes()
    (folder / RESULTS / "0.png").write_bytes(png("white"))
    files.scan(root, state)
    files.scan(root, state)
    assert len(group["candidates"]) == 5
    assert group["candidates"][-1]["version"] == 2
    assert not first["available"]
    (folder / RESULTS / "0.png").unlink()
    assert files.scan(root, state)
    assert files.blob_path(first["sha256"]).read_bytes() == old_bytes


def test_edit_inputs_and_late_prompt_require_explicit_confirmation(project):
    files, root, state, group = project
    folder = root / group["path"]
    (folder / RESULTS / "first.png").write_bytes(png("red"))
    files.scan(root, state)
    files.scan(root, state)
    first = group["candidates"][0]
    assert first["condition_id"] is None
    (folder / INPUTS[0]).write_text("条件 A", encoding="utf-8")
    files.scan(root, state)
    assert first["condition_id"] is None
    (folder / INPUTS[0]).write_text("条件 B", encoding="utf-8")
    (folder / RESULTS / "late.png").write_bytes(png("green"))
    files.scan(root, state)
    files.scan(root, state)
    assert [v["prompt"] for v in group["conditions"]] == ["条件 A", "条件 B"]
    assert all(c["condition_id"] is None for c in group["candidates"])


def test_shared_character_versions_local_reference_copy_and_rename(project):
    files, root, state, group = project
    directory = root / group["path"]
    (directory / INPUTS[0]).write_text("不改背景", encoding="utf-8")
    red = png("red")
    (directory / REFS / "参考.png").write_bytes(red)
    for v, data in (("V1", red), ("V2", png("blue"))):
        base = root / "共享素材" / "角色" / "你" / v
        base.mkdir(parents=True)
        (base / "定妆图.png").write_bytes(data)
    files.scan(root, state)
    files.scan(root, state)
    asset = state["assets"][0]
    assert [v["version_label"] for v in asset["versions"]] == ["V1", "V2"]
    assert len(group["references"]) == 1
    target = {**group, "path": "E01/S1/G2", "conditions": [], "references": [], "candidates": []}
    (directory / INPUTS[0]).write_text("刚刚保存的修改", encoding="utf-8")
    files.copy_group(root, group, target)
    assert (root / target["path"] / INPUTS[0]).read_text(encoding="utf-8") == "刚刚保存的修改"
    assert (root / target["path"] / REFS / "参考.png").read_bytes() == red
    assert list((root / target["path"] / RESULTS).iterdir()) == []
    result = directory / RESULTS / "old.png"
    result.write_bytes(red)
    files.scan(root, state)
    files.scan(root, state)
    take_id = group["candidates"][0]["id"]
    result.rename(result.with_name("new.png"))
    files.scan(root, state)
    files.scan(root, state)
    assert [f["id"] for f in group["candidates"]] == [take_id]
    renamed = result.with_name("new.png")
    outside = root / "暂存.png"
    renamed.rename(outside)
    files.scan(root, state)
    assert not group["candidates"][0]["available"]
    outside.rename(renamed)
    files.scan(root, state)
    files.scan(root, state)
    assert [f["id"] for f in group["candidates"]] == [take_id]
    assert group["candidates"][0]["available"]


def test_invalid_media_and_untrusted_marker_are_not_imported(project):
    files, root, state, group = project
    p = root / group["path"] / RESULTS / "broken.png"
    p.write_bytes(b"actually not an image")
    files.scan(root, state)
    files.scan(root, state)
    assert p.relative_to(root).as_posix() in state["scan_warnings"]
    assert not group["candidates"]
    (root / MARKER).write_text(json.dumps({"project_id": "other"}))
    with pytest.raises(ValueError):
        files.scan(root, state)


def test_audio_shared_asset_holds_playable_standalone_wav(project):
    files, root, state, _ = project
    folder = root / "共享素材/声音/对白/V1"
    folder.mkdir(parents=True)
    filename = folder / "voice.wav"
    with wave.open(str(filename), "wb") as wav:
        wav.setparams((1, 2, 8000, 800, "NONE", ""))
        wav.writeframes(bytes(1600))
    files.scan(root, state)
    files.scan(root, state)
    asset = state["assets"][0]
    assert len(asset["versions"]) == 1, state.get("scan_warnings")
    sound = asset["versions"][0]
    assert (sound["kind"], sound["mime"]) == ("audio", "audio/wav")
    assert files.blob_path(sound["sha256"]).read_bytes() == filename.read_bytes()
def test_one_drop_with_prompt_reference_and_real_video_keeps_one_recipe(project):
    import base64

    files, root, state, group = project
    directory = root / group["path"]
    (directory / REFS / "reference.png").write_bytes(png("white"))
    (directory / INPUTS[0]).write_text("慢动作", encoding="utf-8")
    fixture = Path(__file__).parents[1] / "fixtures/tiny_mp4.b64"
    (directory / RESULTS / "take.mp4").write_bytes(base64.b64decode(fixture.read_bytes()))
    files.scan(root, state)
    files.scan(root, state)
    assert [c["prompt"] for c in group["conditions"]] == ["慢动作"]
    assert len(group["conditions"][0]["references"]) == 1
    assert len(group["candidates"]) == 1, state.get("scan_warnings")
    take = group["candidates"][0]
    assert take["condition_id"] == group["conditions"][0]["id"]
    assert take["mime"] == "video/mp4" and take["metadata"]["duration_seconds"] > 0


def test_mp3_can_be_used_as_shared_dialogue(project):
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("MP3 fixture encoder unavailable")
    files, root, state, _ = project
    directory = root / "共享素材/声音/电话/V1"
    directory.mkdir(parents=True)
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "0.2", str(directory / "clip.mp3")], check=True, capture_output=True, timeout=15)
    files.scan(root, state)
    files.scan(root, state)
    versions = state["assets"][0]["versions"]
    assert len(versions) == 1, state.get("scan_warnings")
    assert versions[0]["mime"] == "audio/mpeg"
