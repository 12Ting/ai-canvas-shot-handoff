"""Collect the chosen versions and their pinned dependencies into a portable review ZIP."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from ai_canvas.application.handoff_editor import episode_shots, item, neighbors

_HASH = re.compile(r"[0-9a-f]{64}")
_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
}


class _Package:
    def __init__(self, state: dict, blobs: Callable[[str], Path], archive: ZipFile):
        self.state = state
        self.blobs = blobs
        self.archive = archive
        self.paths: dict[str, str | None] = {}
        self.text_paths: dict[str, tuple[str, str]] = {}
        self.missing: list[str] = []
        group_titles = Counter((g["shot_id"], g["title"]) for g in state["groups"])
        self.takes = {}
        for group in state["groups"]:
            label = group["title"]
            if group_titles[(group["shot_id"], label)] > 1:
                label = f"{label}（{Path(group['path']).name}）"
            for index, take in enumerate(group["candidates"], 1):
                self.takes[take["id"]] = (group, take, {
                    "group_id": group["id"], "group": group["title"], "group_label": label,
                    "version_label": f"V{take.get('version', index)}",
                })
        self.open_issues = defaultdict(list)
        for issue in state["issues"]:
            if issue["status"] == "open":
                self.open_issues[issue["shot_id"]].append(issue)

    def file(self, record: dict, stem: str | None = None) -> dict:
        digest = record["sha256"]
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("素材的内容摘要无效")
        relative = f"{stem or '依赖媒体/' + digest}.{_EXT.get(record['mime'], 'bin')}"
        ordered_copy = stem and self.paths.get(digest) and self.paths[digest] != relative
        if digest not in self.paths or ordered_copy:
            # Open the pinned blob, not the mutable download path. A missing blob is
            # visible in both indexes; corruption aborts export rather than publishing wrong bytes.
            try:
                source = Path(self.blobs(digest)).open("rb")
            except FileNotFoundError:
                self.paths[digest] = None
                self.missing.append(f"{record['name']}（{digest}）")
            else:
                seen = hashlib.sha256()
                with source, self.archive.open(relative, "w", force_zip64=True) as target:
                    while block := source.read(1024 * 1024):
                        target.write(block)
                        seen.update(block)
                if seen.hexdigest() != digest:
                    raise ValueError(f"素材校验失败：{record['name']}")
                self.paths.setdefault(digest, relative)
        path = relative if ordered_copy and self.paths[digest] else self.paths[digest]
        return {
            "id": record["id"], "name": record["name"], "kind": record["kind"],
            "sha256": digest, "path": path,
        }

    def condition_files(self, condition):
        cid = condition["id"]
        if cid not in self.text_paths:
            base = f"生成条件/C{len(self.text_paths) + 1:04}"
            self.text_paths[cid] = (f"{base}/提示词.txt", f"{base}/生成配置.txt")
            for path, key in zip(self.text_paths[cid], ("prompt", "config")):
                self.archive.writestr(path, condition[key], ZIP_DEFLATED)
        return self.text_paths[cid]

    def pinned(self, binding: dict) -> dict:
        asset = item(self.state["assets"], binding["asset_id"], "引用的共享素材")
        version = item(asset["versions"], binding["version_id"], "固定的共享素材版本")
        return {**binding, "name": asset["name"],
                "version_label": version.get("version_label") or f"V{asset['versions'].index(version) + 1}",
                "file": self.file(version)}

    def candidate(self, identity: str | None, *, frozen_id=..., stem=None) -> dict | None:
        if identity is None:
            return None
        if identity not in self.takes:
            raise ValueError("找不到候选版本")
        group, candidate, labels = self.takes[identity]
        cid = candidate.get("condition_id") if frozen_id is ... else frozen_id
        condition = item(group["conditions"], cid, "候选固定的生成条件") if cid else None
        result = {
            "id": identity, **labels,
            "file": self.file(candidate, stem=stem), "condition": None,
        }
        if condition is not None:
            prompt_file, config_file = self.condition_files(condition)
            result["condition"] = {
                "id": cid, "prompt": condition["prompt"], "config": condition["config"],
                "prompt_file": prompt_file, "config_file": config_file,
                "references": [self.file(ref) for ref in condition["references"]],
                "bindings": [self.pinned(pin) for pin in condition["bindings"]],
            }
        return result

    def shot(self, shot: dict, stem: str) -> dict:
        selected = self.candidate(shot.get("selected_id"), stem=stem)
        if selected:
            _, take, _ = self.takes[selected["id"]]
            selected["selection_reason"] = take.get("selection_reason", "")
        pending = []
        for issue in self.open_issues[shot["id"]]:
            _, take, labels = self.takes[issue["candidate_id"]]
            pending.append({"id": issue["id"], "note": issue["note"], "candidate_id": take["id"],
                            "name": take["name"], **labels})
        return {
            "id": shot["id"], "title": shot["title"], "order": shot["order"],
            "selected": selected, "open_issues": pending,
            "dialogue": shot["dialogue"], "dialogue_version_id": shot.get("dialogue_version_id"),
            "sounds": [self.pinned(sound) for sound in shot["sounds"]],
        }

    def issue(self, issue: dict) -> dict:
        shot = item(self.state["shots"], issue["shot_id"], "意见对应的镜头")
        episode = item(self.state["episodes"], shot["episode_id"], "意见对应的集")
        context = issue["context"]
        original = self.candidate(issue["candidate_id"], frozen_id=issue["condition_id"], stem="补镜/原版本")
        _, source, _ = self.takes[issue["candidate_id"]]
        current = None
        if source.get("condition_id") != issue["condition_id"]:
            current = self.candidate(source["id"])
        adjacent = []
        for key, neighbor in zip(("previous", "next"), neighbors(self.state, shot)):
            adjacent.append({
                "side": key, "then_shot_id": context.get(f"{key}_shot_id"),
                "then": self.candidate(context.get(f"{key}_id"), frozen_id=context.get(f"{key}_condition_id")),
                "temporary": context.get(f"{key}_temporary", False),
                "now_shot_id": neighbor["id"] if neighbor else None,
                "now": self.candidate(neighbor.get("selected_id") if neighbor else None),
            })
        return {
            "id": issue["id"], "shot_id": shot["id"], "shot": shot["title"],
            "episode_id": episode["id"], "episode": episode["title"], "note": issue["note"],
            "status": issue["status"], "original": original, "current_record": current,
            "adjacent": adjacent, "repair": self.candidate(issue.get("repair_id")),
            "dialogue": context["dialogue"], "dialogue_version_id": context.get("dialogue_version_id"),
            "sounds": [self.pinned(sound) for sound in context["sounds"]],
            "current_dialogue": shot["dialogue"], "current_dialogue_version_id": shot.get("dialogue_version_id"),
            "current_sounds": [self.pinned(sound) for sound in shot["sounds"]],
        }


def _file_link(record: dict, *, player=False) -> str:
    name = escape(record["name"])
    if not record["path"]:
        return f"<span>文件缺失 · {name}</span>"
    path = escape(record["path"], quote=True)
    link = f'<a href="{path}">{name}</a>'
    if player and record["kind"] in ("audio", "video"):
        tag = record["kind"]
        return f'{link} <{tag} controls preload="none" src="{path}"></{tag}>'
    return link


def _take_html(candidate: dict | None, *, selected=False) -> str:
    if not candidate:
        return "<p>待选</p>"
    prefix = "采用版 · " if selected else ""
    return (f'<p>{prefix}{escape(candidate["group_label"])} / {escape(candidate["version_label"])} / '
            f'{_file_link(candidate["file"], player=True)}</p>')


def _condition_html(condition) -> str:
    if condition is None:
        return "<p>生成条件未记录或待核对</p>"
    parts = []
    for label, key in (("提示词", "prompt"), ("生成配置", "config")):
        parts.append(f'<h4><a href="{condition[key + "_file"]}">{label}</a></h4>'
                     f'<pre>{escape(condition[key]) or "未记录"}</pre>')
    for ref in condition["references"]:
        parts.append(f"<p>参考素材 · {_file_link(ref)}</p>")
    for binding in condition["bindings"]:
        parts.append(f'<p>共享素材 · {escape(binding["name"])} {escape(binding["version_label"])} · '
                     f'{_file_link(binding["file"])}</p>')
    return "".join(parts)


def _candidate_html(candidate: dict | None) -> str:
    return _take_html(candidate) + (_condition_html(candidate["condition"]) if candidate else "")


def _performance(text, sounds):
    roles = {"temp": "临时配音", "final": "正式配音", "reference": "声音参考"}
    parts = [f"<p>台词</p><pre>{escape(text) or '未记录'}</pre>"]
    for sound in sounds:
        label = roles[sound["role"]]
        parts.append(f'<p>{label} · {escape(sound["name"])} {escape(sound["version_label"])} · '
                     f'{_file_link(sound["file"], player=True)}</p>')
    return "".join(parts)


def _html(manifest):
    parts = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width, initial-scale=1"><title>镜头交接</title>'
        '<style>body{font:16px/1.6 system-ui,sans-serif;max-width:900px;margin:auto;padding:24px}'
        'video{display:block;width:min(100%,520px);max-height:360px;background:#152026}'
        'audio{display:block;max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;'
        'background:#f3f4f6;padding:12px}section{border-top:1px solid #bbb}a{overflow-wrap:anywhere}'
        'details{margin:10px 0}</style>',
        f'<h1>{escape(manifest["name"])} · 镜头交接</h1>',
        '<p>先解压整个文件夹，再打开本页。原始媒体可直接下载或拖入剪辑软件。</p>',
    ]
    if manifest["missing"]:
        parts.append("<h2>缺少依赖文件</h2><ul>")
        parts.extend(f"<li>{escape(message)}</li>" for message in manifest["missing"])
        parts.append("</ul>")
    for episode in manifest["episodes"]:
        parts.append(f'<h2>{escape(episode["title"])}</h2>')
        for index, shot in enumerate(episode["shots"], 1):
            parts.append(f'<section><h3>{index}. {escape(shot["title"])}</h3>')
            selected = shot["selected"]
            parts.append(_take_html(selected, selected=True))
            if selected and selected["selection_reason"]:
                parts.append(f'<p>采用理由 · {escape(selected["selection_reason"])}</p>')
            if shot["open_issues"]:
                parts.append("<h4>待补镜</h4><ul>")
                for issue in shot["open_issues"]:
                    parts.append(f'<li>{escape(issue["group_label"])} / {escape(issue["version_label"])} / '
                                 f'{escape(issue["name"])} · {escape(issue["note"])}</li>')
                parts.append("</ul>")
            if selected or shot["dialogue"] or shot["sounds"]:
                parts.append("<details><summary>生成资料与表演</summary>")
                if selected:
                    parts.append(_condition_html(selected["condition"]))
                parts.append(_performance(shot["dialogue"], shot["sounds"]))
                parts.append("</details>")
            parts.append("</section>")
    for issue in manifest["issues"]:
        status = "待补镜" if issue["status"] == "open" else "已解决"
        parts.extend((
            f'<h2>{status} · {escape(issue["episode"])} / {escape(issue["shot"])}</h2>',
            f'<pre>{escape(issue["note"])}</pre><h3>问题原版本及记录时的条件</h3>',
            _candidate_html(issue["original"]), "<h3>记录时的表演</h3>",
            _performance(issue["dialogue"], issue["sounds"]),
        ))
        if issue["current_record"]:
            parts.extend(("<h3>当前补记的生成资料</h3>", _candidate_html(issue["current_record"])))
        for row in issue["adjacent"]:
            label = "前镜" if row["side"] == "previous" else "后镜"
            if (row["then_shot_id"] == row["now_shot_id"] and row["then"] == row["now"]
                    and not row["temporary"]):
                parts.append(f"<details><summary>{label} · 记录时与当前相同</summary>")
                parts.append(_candidate_html(row["then"]) if row["then_shot_id"] else "<p>本集无此邻镜</p>")
                parts.append("</details>")
                continue
            for point, title in (("then", "记录时"), ("now", "当前")):
                temporary = " · 临时对照" if point == "then" and row["temporary"] else ""
                parts.append(f"<details><summary>{title}{label}{temporary}</summary>")
                parts.append(_candidate_html(row[point]) if row[f"{point}_shot_id"] else "<p>本集无此邻镜</p>")
                parts.append("</details>")
        if issue["repair"]:
            parts.extend(("<h3>修复候选</h3>", _candidate_html(issue["repair"])))
        if issue["dialogue"] != issue["current_dialogue"] or issue["sounds"] != issue["current_sounds"]:
            parts.extend(("<h3>当前表演</h3>", _performance(issue["current_dialogue"], issue["current_sounds"])))
    return "".join(parts) + "</html>"


def export_handoff(
    state: dict, blob_path_callable: Callable[[str], Path], destination: Path, issue_id: str | None = None,
) -> None:
    """Write a self-contained ZIP to a caller-owned temporary path."""
    with ZipFile(destination, "w", ZIP_STORED, allowZip64=True) as archive:
        pkg = _Package(state, blob_path_callable, archive)
        manifest = {
            "schema_version": 1, "project_id": state["project_id"], "name": state["name"],
            "episodes": [], "issues": [], "missing": pkg.missing,
        }
        if issue_id is None:
            for number, episode in enumerate(state["episodes"], 1):
                shots = [pkg.shot(shot, f"定稿/EP{number:03}/SH{index:04}")
                         for index, shot in enumerate(episode_shots(state, episode["id"]), 1)]
                manifest["episodes"].append({"id": episode["id"], "title": episode["title"], "shots": shots})
        else:
            manifest["issues"].append(pkg.issue(item(state["issues"], issue_id, "补镜意见")))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), ZIP_DEFLATED)
        archive.writestr("index.html", _html(manifest), ZIP_DEFLATED)
