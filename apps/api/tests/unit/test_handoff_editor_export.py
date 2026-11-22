import hashlib
import json
from copy import deepcopy
from zipfile import ZipFile

import pytest

from ai_canvas.application.handoff_editor import apply_edit, asset_usage
from ai_canvas.application.handoff_export import export_handoff


@pytest.fixture
def project(tmp_path):
    blobs = tmp_path / "blobs"
    blobs.mkdir()

    def media(identity, kind="video", mime="video/mp4"):
        content = ("fixed bytes: " + identity).encode()
        digest = hashlib.sha256(content).hexdigest()
        (blobs / digest).write_bytes(content)
        return {"id": identity, "kind": kind, "mime": mime, "sha256": digest,
                "name": identity, "path": f"EP01/SH01/G01/生成结果/{identity}"}

    state = {
        "project_id": "P", "name": "离线短片", "episodes": [{"id": "E", "title": "第一集"},
                                                           {"id": "F", "title": "第二集"}],
        "shots": [], "groups": [], "assets": [], "issues": [],
    }
    image = media("角色原图", "image", "image/png")
    state["assets"] = [
        {"id": "character", "kind": "character", "name": "女孩", "versions": [image]},
        {"id": "audio", "kind": "sound", "name": "对白", "versions": [
            media("音频1", "audio", "audio/wav"), media("音频2", "audio", "audio/mpeg"),
        ]},
    ]
    for index, shot_id in enumerate(("left", "subject", "right", "other")):
        shot = {"id": shot_id, "episode_id": "F" if shot_id == "other" else "E", "order": index,
                "title": shot_id, "selected_id": None, "sounds": [], "dialogue": ""}
        candidate = {**media("video-" + shot_id), "condition_id": None}
        group = {"id": "grp-" + shot_id, "shot_id": shot_id, "title": "条件组", "path": shot_id,
                 "references": [deepcopy(image)], "conditions": [], "bindings": [], "candidates": [candidate]}
        state["shots"].append(shot)
        state["groups"].append(group)
    return state, blobs


def edit(state, action, **data):
    apply_edit(state, action, data)


def test_review_pins_original_and_neighbors_separately_from_current_edit(project):
    state, _ = project
    edit(state, "select_candidate", shot_id="left", candidate_id="video-left")
    edit(state, "open_issue", candidate_id="video-subject", note="转身方向", next_id="video-right")
    issue = state["issues"][0]
    assert issue["context"]["next_temporary"]
    assert state["shots"][2]["selected_id"] is None
    assert issue["context"]["previous_id"] == "video-left"
    edit(state, "reorder_shots", episode_id="E", shot_ids=["right", "left", "subject"])
    assert issue["context"]["next_shot_id"] == "right"
    edit(state, "select_candidate", shot_id="left", candidate_id=None)
    assert issue["context"]["previous_id"] == "video-left"
    assert issue["status"] == "open"
    with pytest.raises(ValueError):
        edit(state, "resolve_issue", issue_id=issue["id"], candidate_id="video-other")
    with pytest.raises(ValueError):
        edit(state, "resolve_issue", issue_id=issue["id"], candidate_id="video-subject")
    edit(state, "resolve_issue", issue_id=issue["id"])
    assert issue["status"] == "resolved"


def test_exact_generation_and_performance_versions_survive_replacement(project):
    state, _ = project
    for group in (state["groups"][1], state["groups"][3]):
        edit(state, "bind_asset", group_id=group["id"], asset_id="character", version_id="角色原图")
        edit(state, "set_conditions", group_id=group["id"], condition_id=group["conditions"][0]["id"],
             candidate_ids=[group["candidates"][0]["id"]])
    assert {r["episode_id"] for r in asset_usage(state, "character", "角色原图")} == {"E", "F"}
    group = state["groups"][1]
    edit(state, "bind_asset", group_id=group["id"], asset_id="character", version_id=None)
    assert group["candidates"][0]["condition_id"] == group["conditions"][0]["id"]
    for shot_id in ("subject", "other"):
        edit(state, "set_sound", shot_id=shot_id, role="final", asset_id="audio", version_id="音频1")
    edit(state, "set_dialogue", shot_id="subject", text="你来了。")
    edit(state, "link_dialogue", shot_id="other", source_shot_id="subject")
    assert state["shots"][1]["dialogue_version_id"] == state["shots"][3]["dialogue_version_id"]
    edit(state, "open_issue", candidate_id="video-subject", note="转头")
    edit(state, "set_sound", shot_id="subject", role="final", asset_id="audio", version_id="音频2")
    edit(state, "set_dialogue", shot_id="subject", text="停一下。")
    assert state["shots"][3]["dialogue"] == state["issues"][0]["context"]["dialogue"] == "你来了。"
    assert state["shots"][1]["sound_history"][-1]["sounds"][0]["version_id"] == "音频1"
    assert state["shots"][3]["sounds"][0]["version_id"] == "音频1"
    assert state["issues"][0]["context"]["sounds"][0]["version_id"] == "音频1"


def test_invalid_batch_and_cross_episode_order_are_atomic(project):
    state, _ = project
    edit(state, "bind_asset", group_id="grp-subject", asset_id="character", version_id="角色原图")
    cid = state["groups"][1]["conditions"][0]["id"]
    initial = deepcopy(state)
    for action, data in (
        ("reorder_shots", {"episode_id": "E", "shot_ids": ["left", "right", "other"]}),
        ("select_candidate", {"shot_id": "subject", "candidate_id": "video-other"}),
        ("select_candidate", {"shot_id": "subject"}),
        ("select_candidate", {"shot_id": "subject", "candidate_id": "video-subject", "reason": "超" * 501}),
        ("select_candidate", {"shot_id": "subject", "candidate_id": None, "reason": "悬空"}),
        ("save_selection_reason", {"shot_id": "subject", "candidate_id": "video-other", "reason": "串镜"}),
        ("save_selection_reason", {"shot_id": "subject", "candidate_id": "video-subject"}),
        ("set_conditions", {"group_id": "grp-subject", "condition_id": cid,
                            "candidate_ids": ["video-subject", "video-other"]}),
        ("set_sound", {"shot_id": "subject", "asset_id": "audio", "version_id": "角色原图", "role": "final"}),
        ("open_issue", {"candidate_id": "video-left", "previous_id": "video-other", "note": "错误对照"}),
    ):
        with pytest.raises(ValueError):
            apply_edit(state, action, data)
        assert state == initial


def test_selection_reason_belongs_to_the_take_and_can_be_cleared(project):
    state, _ = project
    shot = state["shots"][1]
    first = state["groups"][1]["candidates"][0]
    second = dict(first, id="second-take")
    state["groups"][1]["candidates"].append(second)
    edit(state, "select_candidate", shot_id=shot["id"], candidate_id=first["id"], reason="  目光对上了  ")
    assert first["selection_reason"] == "目光对上了"
    edit(state, "select_candidate", shot_id=shot["id"], candidate_id=second["id"])
    assert second.get("selection_reason", "") == ""
    edit(state, "save_selection_reason", shot_id=shot["id"], candidate_id=first["id"], reason="保留停顿")
    assert shot["selected_id"] == second["id"]
    edit(state, "select_candidate", shot_id=shot["id"], candidate_id=None)
    edit(state, "select_candidate", shot_id=shot["id"], candidate_id=first["id"])
    assert first["selection_reason"] == "保留停顿"
    edit(state, "save_selection_reason", shot_id=shot["id"], candidate_id=first["id"], reason="   ")
    assert shot["selected_id"] == first["id"] and first["selection_reason"] == ""


def test_portable_issue_package_has_exact_versions_and_reports_late_data(project, tmp_path):
    state, blobs = project
    edit(state, "select_candidate", shot_id="left", candidate_id="video-left")
    edit(state, "open_issue", candidate_id="video-subject", note="<script>unsafe()</script>")
    edit(state, "bind_asset", group_id="grp-subject", asset_id="character", version_id="角色原图")
    cid = state["groups"][1]["conditions"][-1]["id"]
    edit(state, "set_conditions", group_id="grp-subject", condition_id=cid, candidate_ids=["video-subject"])
    issue_id = state["issues"][0]["id"]
    output = tmp_path / "补镜.zip"
    export_handoff(state, lambda digest: blobs / digest, output, issue_id)
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        issue = manifest["issues"][0]
        assert issue["original"]["condition"] is None
        assert issue["current_record"]["condition"]["id"] == cid
        assert issue["adjacent"][1]["then"] is None
        assert manifest["missing"] == []
        assert "<script>" not in archive.read("index.html").decode()
        assert "EP01/SH01/G01" not in archive.read("manifest.json").decode()
        assert "定稿/EP001" not in "".join(archive.namelist())
        media_file = issue["original"]["file"]
        assert hashlib.sha256(archive.read(media_file["path"])).hexdigest() == media_file["sha256"]
        condition = issue["current_record"]["condition"]
        assert archive.read(condition["prompt_file"]).decode() == condition["prompt"]
        assert condition["references"][0]["path"] == condition["bindings"][0]["file"]["path"]


def test_project_selects_list_missing_blobs_and_unselected_shots(project, tmp_path):
    state, blobs = project
    edit(state, "select_candidate", shot_id="subject", candidate_id="video-subject")
    file = state["groups"][1]["candidates"][0]
    source = blobs / file["sha256"]
    source.unlink()
    output = tmp_path / "选片.zip"
    export_handoff(state, lambda digest: blobs / digest, output)
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["episodes"][0]["shots"][0]["selected"] is None
        assert manifest["episodes"][0]["shots"][1]["selected"]["file"]["path"] is None
        assert manifest["episodes"][0]["shots"][1]["selected"]["selection_reason"] == ""
        assert manifest["episodes"][0]["shots"][1]["selected"]["version_label"] == "V1"
        assert len(manifest["missing"]) == 1
        html = archive.read("index.html").decode()
        assert "缺少依赖文件" in html
        assert "采用理由" not in html and "待补镜" not in html
    source.write_bytes(b"corrupted bytes")
    with pytest.raises(ValueError, match="校验失败"):
        export_handoff(state, lambda digest: blobs / digest, output)


def test_project_package_names_the_selected_take_and_each_open_issue_source(project, tmp_path):
    state, blobs = project
    group = state["groups"][1]
    group.update(title="夜<景>", path="EP01/SH02/G01")
    first = group["candidates"][0]
    first.update(name="回头.mp4", version=1, available=False)
    overwritten = dict(state["groups"][2]["candidates"][0], id="overwrite", name=first["name"], version=2)
    group["candidates"].append(overwritten)
    other_group = dict(group, id="alternative", path="EP01/SH02/G02")
    chosen = dict(state["groups"][3]["candidates"][0], id="chosen", name=first["name"], version=1)
    other_group["candidates"] = [chosen]
    state["groups"].append(other_group)

    edit(state, "select_candidate", shot_id="subject", candidate_id=first["id"], reason="曾采用")
    edit(state, "open_issue", candidate_id=first["id"], note="保留衣服 <抬手>")
    edit(state, "open_issue", candidate_id=overwritten["id"], note="目光再低一点")
    edit(state, "select_candidate", shot_id="subject", candidate_id=chosen["id"], reason="停顿 & 节奏")
    edit(state, "open_issue", candidate_id=chosen["id"], note="缩短尾帧")
    edit(state, "open_issue", candidate_id=chosen["id"], note="已处理的意见")
    edit(state, "resolve_issue", issue_id=state["issues"][-1]["id"])
    edit(state, "open_issue", candidate_id="video-left", note="待选镜头也需补光")
    original_issues = deepcopy(state["issues"])

    output = tmp_path / "选片交接.zip"
    export_handoff(state, lambda digest: blobs / digest, output)
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        left, subject, right = manifest["episodes"][0]["shots"]
        selected = subject["selected"]
        assert (selected["id"], selected["group_id"], selected["group_label"], selected["version_label"],
                selected["file"]["name"], selected["selection_reason"]) == (
                    "chosen", "alternative", "夜<景>（G02）", "V1", "回头.mp4", "停顿 & 节奏")
        assert archive.read(selected["file"]["path"]) == (blobs / chosen["sha256"]).read_bytes()
        assert [p for p in archive.namelist() if p.endswith(".mp4")] == [selected["file"]["path"]]
        assert [(note["candidate_id"], note["group_label"], note["version_label"], note["name"], note["note"])
                for note in subject["open_issues"]] == [
                    (first["id"], "夜<景>（G01）", "V1", "回头.mp4", "保留衣服 <抬手>"),
                    ("overwrite", "夜<景>（G01）", "V2", "回头.mp4", "目光再低一点"),
                    ("chosen", "夜<景>（G02）", "V1", "回头.mp4", "缩短尾帧"),
                ]
        assert left["selected"] is None and left["open_issues"][0]["candidate_id"] == "video-left"
        assert right["open_issues"] == []
        html = archive.read("index.html").decode()
        assert "采用版 · 夜&lt;景&gt;（G02） / V1 /" in html and "采用理由 · 停顿 &amp; 节奏" in html
        assert "夜&lt;景&gt;（G01） / V2 / 回头.mp4 · 目光再低一点" in html
        assert "待选镜头也需补光" in html and "保留衣服 &lt;抬手&gt;" in html
        assert "曾采用" not in html and "已处理的意见" not in html
    assert state["issues"] == original_issues
