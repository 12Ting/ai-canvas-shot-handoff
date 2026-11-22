"""Create an isolated local project and verify the selected shots and optional issue handoffs."""
import argparse
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
from ai_canvas.config import Settings  # noqa: E402
from ai_canvas.main import create_app  # noqa: E402
from handoff_demo_sources import original  # noqa: E402


class LocalPicker:
    def __init__(self, directory):
        self.directory = directory

    def choose_directory(self):
        return self.directory


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("href", "src"):
                self.links.append(value)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as src:
        while block := src.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def read_package(archive_file, receiver):
    """Verify with only the unpacked files at the receiving location."""
    with ZipFile(archive_file) as archive:
        for name in archive.namelist():
            require((receiver / name).resolve().is_relative_to(receiver.resolve()), "交接路径越界")
        archive.extractall(receiver)
    manifest = json.loads((receiver / "manifest.json").read_text(encoding="utf-8"))
    require(not manifest["missing"], "交接包报告缺少依赖")
    parser = PageLinks()
    parser.feed((receiver / "index.html").read_text(encoding="utf-8"))
    for link in parser.links:
        target = (receiver / link).resolve()
        require(target.is_relative_to(receiver.resolve()) and target.is_file(), "离线页面含失效链接")
    checked = set()

    def check(row):
        if isinstance(row, dict):
            if "sha256" in row:
                target = (receiver / row["path"]).resolve()
                require(target.is_relative_to(receiver.resolve()) and digest(target) == row["sha256"],
                        "交接文件的路径或内容不一致")
                checked.add(row["sha256"])
            for item in row.values():
                check(item)
        elif isinstance(row, list):
            for item in row:
                check(item)

    check(manifest)
    return manifest, len(checked), len(parser.links)


def ok(response):
    if not response.is_success:
        raise RuntimeError(f"本机 API {response.request.url.path} 失败：{response.status_code} {response.text[:500]}")
    return response


def prepare(out):
    state_dir = out / "database"
    work = out / "workspace"
    work.mkdir(parents=True)
    title = "纸船选片样例"
    shots = original(out / "original-media")
    target_index = 1
    app = create_app(Settings(data_dir=state_dir, handoff_parent=None, public_origin="http://testserver",
                              open_browser=False))
    app.state.native_media_picker = LocalPicker(work)
    with TestClient(app, base_url="http://testserver", headers={"Origin": "http://testserver"}) as client:
        token = ok(client.post("/api/v1/session/exchange", headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
        })).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        state = ok(client.post("/api/v1/handoff/projects", json={"name": title})).json()
        endpoint = "/api/v1/projects/" + state["project_id"] + "/handoff"

        def act(action, **data):
            return ok(client.post(endpoint + "/actions", json={"action": action, "data": data})).json()

        group_specs = []
        sound_specs = []
        shot_ids = []
        for source_shot in shots:
            state = act("add_shot", episode_id=state["episodes"][0]["id"], title=source_shot.title)
            sid = state["shots"][-1]["id"]
            shot_ids.append(sid)
            if source_shot.dialogue:
                state = act("set_dialogue", shot_id=sid, text=source_shot.dialogue)
            for take in source_shot.takes:
                state = act("add_group", shot_id=sid, title=take.label)
                group = state["groups"][-1]
                folder = work / title / group["path"]
                shutil.copy2(take.reference, folder / "参考素材" / take.reference.name)
                (folder / "提示词.txt").write_text(take.prompt, encoding="utf-8", newline="\n")
                (folder / "生成配置.txt").write_text(take.config, encoding="utf-8", newline="\n")
                result_path = folder / "生成结果" / take.video.name
                shutil.copy2(take.video, result_path)
                group_specs.append((sid, group["id"], take, result_path))
            for audio in source_shot.sounds:
                state = act("add_asset", kind="sound", name=audio.stem)
                asset = state["assets"][-1]
                dest = work / title / asset["path"] / "V1" / audio.name
                shutil.copy2(audio, dest)
                sound_specs.append((sid, asset["id"], audio))
        deadline = time.monotonic() + 180
        while True:
            state = act("scan")
            if all(g["candidates"] and g["references"] for g in state["groups"]) and all(
                a["versions"] for a in state["assets"]
            ):
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"目录扫描未完成：{state.get('scan_warnings')}")
            time.sleep(0.65)

        indexed = {g["id"]: g for g in state["groups"]}
        by_shot = {}
        locate_count = 0
        for sid, gid, take, result_path in group_specs:
            group = indexed[gid]
            matches = ok(client.get(endpoint + "/locate", params={"query": str(result_path)})).json()["matches"]
            require(len(matches) == 1 and matches[0]["group_id"] == gid, "结果路径定位错误")
            candidate = next(c for c in group["candidates"] if c["id"] == matches[0]["candidate_id"])
            require(candidate["sha256"] == digest(take.video), "候选与源片不符")
            condition = group["conditions"][-1]
            require(condition["config"] == take.config and condition["prompt"] == take.prompt
                    and any(f["sha256"] == digest(take.reference) for f in condition["references"]),
                    "参考、配置或文字版本不符")
            if candidate["condition_id"] != condition["id"]:
                act("set_conditions", group_id=gid, condition_id=condition["id"], candidate_ids=[candidate["id"]])
            by_shot.setdefault(sid, []).append(candidate)
            locate_count += 1

        for sid, aid, audio in sound_specs:
            asset = next(a for a in state["assets"] if a["id"] == aid)
            version = asset["versions"][-1]
            require(version["sha256"] == digest(audio), "音频与时间线源文件不符")
            state = act("set_sound", shot_id=sid, asset_id=aid, version_id=version["id"], role="reference")
        for index, sid in enumerate(shot_ids):
            if index != target_index:
                state = act("select_candidate", shot_id=sid, candidate_id=by_shot[sid][-1]["id"])
        subject = shot_ids[target_index]
        candidate = by_shot[subject][0]
        reason = "纸船和前后镜都向右，剪在一起方向连贯。"
        state = act("select_candidate", shot_id=subject, candidate_id=by_shot[subject][-1]["id"], reason=reason)
        note = "纸船向左，与前后镜相反。请改为向右，保留提示音。"
        state = act("open_issue", candidate_id=candidate["id"], note=note)
        issue_id = state["issues"][-1]["id"]
        for neighbor, edge in ((target_index - 1, "last"), (target_index + 1, "first")):
            clip = by_shot[shot_ids[neighbor]][-1]
            frame = ok(client.get(f"{endpoint}/files/{clip['sha256']}/frame", params={"edge": edge}))
            with Image.open(io.BytesIO(frame.content)) as still:
                require(still.format == "JPEG" and min(still.size) > 0, "邻镜抽帧无效")
        for filename, params in (("shot-issue", {"issue_id": issue_id}), ("selected-shots", {})):
            (out / (filename + ".zip")).write_bytes(ok(client.get(endpoint + "/export", params=params)).content)

    report = {
        "mode": "可再分发的程序合成样例",
        "project_id": state["project_id"], "issue_id": issue_id, "shots": len(shots),
        "groups": len(group_specs), "path_matches_verified": locate_count,
        "individual_audio_files_verified": len(sound_specs), "neighbor_jpeg_probes": 2,
        "packages": {},
    }
    for filename in ("shot-issue", "selected-shots"):
        manifest, count, links = read_package(out / (filename + ".zip"), out / "receiver" / filename)
        if filename == "shot-issue":
            issue = manifest["issues"][0]
            require(issue["original"]["file"]["sha256"] == candidate["sha256"] and issue["note"] == note
                    and issue["status"] == "open" and len(issue["sounds"]) == len(shots[target_index].sounds),
                    "接手目录中的问题或表演信息不符")
            require(issue["dialogue"] == shots[target_index].dialogue, "接手目录的台词版本不符")
            original_take = shots[target_index].takes[0]
            condition = issue["original"]["condition"]
            require(condition["prompt"] == original_take.prompt and condition["config"] == original_take.config
                    and {f["sha256"] for f in condition["references"]} == {digest(original_take.reference)},
                    "交接包未保留原问题版本的生成资料")
            require({s["file"]["sha256"] for s in issue["sounds"]} == {digest(p) for p in shots[target_index].sounds},
                    "交接包未保留相关的准确音频版本")
            for neighbor, index in zip(issue["adjacent"], (target_index - 1, target_index + 1)):
                require(neighbor["then"]["file"]["sha256"] == digest(shots[index].takes[-1].video),
                        "交接包中前后镜采用的版本不符")
        else:
            for index, row in enumerate(manifest["episodes"][0]["shots"]):
                require(row["selected"]["file"]["sha256"] == digest(shots[index].takes[-1].video),
                        "选片包选错了镜头版本")
                issues = row["open_issues"]
                if index == target_index:
                    require(len(issues) == 1 and issues[0]["candidate_id"] == candidate["id"]
                            and issues[0]["name"] == candidate["name"] and issues[0]["note"] == note,
                            "选片包未指明哪一版有待改意见")
                    require(row["selected"]["selection_reason"] == reason and
                            row["selected"]["id"] != issues[0]["candidate_id"], "意见归属或采用理由错误")
                else:
                    require(not issues, "待改意见出现在其他镜头")
        report["packages"][filename] = {"verified_blob_hashes": count, "offline_links": links}
    (out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="全新样例输出目录；使用 --open 可重新打开")
    parser.add_argument("--open", action="store_true", help="打开已验证样例的本地发行页面")
    args = parser.parse_args()
    out = args.out.resolve()
    if args.open:
        if not (out / "verification.json").is_file():
            parser.error("--open 需要已有的样例输出目录")
        dist = ROOT / "apps/web/dist"
        if not (dist / "index.html").is_file():
            parser.error("请先在 apps/web 执行 npm run build")
        from ai_canvas.launcher import main as launch
        os.environ.update(AI_CANVAS_DATA_DIR=str(out / "database"), AI_CANVAS_WEB_DIST=str(dist),
                          AI_CANVAS_AUTH_MODE="session", AI_CANVAS_RUNTIME_PROFILE="release",
                          AI_CANVAS_OPEN_BROWSER="true", AI_CANVAS_LISTEN_PORT="0")
        launch()
        return
    if out.exists():
        parser.error("输出目录已存在；请指定新目录，已有样例可使用 --open")
    out.mkdir(parents=True)
    report = prepare(out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"离线交接预览：{out / 'receiver' / 'selected-shots' / 'index.html'}")


if __name__ == "__main__":
    main()
