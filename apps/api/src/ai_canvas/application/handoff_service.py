"""Local, single-owner directory projects. SQLite owns editorial choices; the folders own inputs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from uuid import UUID, uuid4

from sqlalchemy import select

from ai_canvas.application.handoff_editor import apply_edit
from ai_canvas.application.handoff_files import HandoffFiles, MARKER, REFS, RESULTS, SHARED
from ai_canvas.application.handoff_graph import (
    active_graph,
    reconcile_graph,
    reflect_edit,
    register_take,
    shot_record,
)
from ai_canvas.application.handoff_snapshots import signature
from ai_canvas.infrastructure.artifacts.store import sanitize_filename
from ai_canvas.infrastructure.db.models import HandoffProjectRow

log = logging.getLogger(__name__)


class HandoffConflict(ValueError):
    pass


def _new_state(project_id, name):
    return dict(
        project_id=project_id,
        name=name,
        episodes=[dict(id=str(uuid4()), title="第 1 集", path="EP01")],
        shots=[],
        groups=[],
        assets=[],
        issues=[],
        scan_warnings={},
    )


def _item(items, key):
    for item in items:
        if item["id"] == key:
            return item
    raise LookupError("找不到指定的项目条目")


def _title(data, default):
    value = data.get("title", default)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValueError("名称需要 1 至 200 个字符")
    return value.strip()


def files_in(state):
    for group in state["groups"]:
        yield from group["candidates"]
        yield from group["references"]
        for condition in group["conditions"]:
            yield from condition["references"]
    for asset in state["assets"]:
        yield from asset["versions"]


def _view(state, version, *, for_export=False):
    visible = {s["id"] for s in state["shots"]}
    view = {k: value for k, value in state.items() if not k.startswith("_")}
    view["version"] = version
    view["issues"] = [issue for issue in state["issues"] if issue["shot_id"] in visible]
    if not for_export:
        view["groups"] = [g for g in state["groups"] if g["shot_id"] in visible]
    return view


def _matches_file(path, item):
    if not path.is_file() or path.stat().st_size != item["byte_size"]:
        return False
    before = signature(path.stat())
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return signature(path.stat()) == before and digest.hexdigest() == item["sha256"]


class HandoffService:
    def __init__(self, sessions, projects, graphs, gate, settings, picker):
        self.sessions, self.projects, self.graphs = sessions, projects, graphs
        self.gate, self.settings = gate, settings
        self.files = HandoffFiles(settings.data_dir, picker, settings=settings)
        self._locks = {}
        self._guard = Lock()
        self._creation = Lock()
        self._stop = Event()
        self._worker = Thread(target=self._monitor, daemon=True, name="canvas-directory-sync")

    def start(self):
        self._worker.start()

    def close(self):
        self._stop.set()
        if self._worker.is_alive():
            self._worker.join()

    def _lock(self, project_id):
        with self._guard:
            return self._locks.setdefault(project_id, RLock())

    def _monitor(self):
        # Polling also covers files changed while the application was closed and dropped OS events.
        while not self._stop.wait(3):
            try:
                with self.sessions() as session:
                    ids = list(session.scalars(select(HandoffProjectRow.project_id)))
                for key in ids:
                    if self._stop.is_set():
                        return
                    try:
                        self.scan(key)
                    except (ValueError, OSError, LookupError) as exc:
                        log.debug("Directory scan unavailable: %s", exc)
            except Exception:
                log.exception("Directory scan interrupted")

    @staticmethod
    def _row(session, key):
        row = session.get(HandoffProjectRow, key)
        if row is None:
            raise LookupError("这个项目尚未连接工作目录")
        return row

    @staticmethod
    def _store(row, state):
        value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if row.document != value:
            row.document, row.version = value, row.version + 1
        return _view(state, row.version)

    def create(self, name):
        root = self.files.project_root(name)
        with self._creation:
            if root.exists():
                try:
                    key = self.files.root_id(root)
                except (ValueError, OSError) as exc:
                    raise ValueError("所选位置已有同名目录，请更换项目名称或父目录") from exc
                with self.sessions() as session:
                    row = session.get(HandoffProjectRow, key)
                    if row is not None:
                        if Path(row.root) != root or json.loads(row.document)["name"] != name:
                            raise ValueError("这个目录属于另一个项目或其副本")
                if row is not None:
                    return self.get(key)
                # Recover a creation interrupted between writing the marker and committing SQLite.
                if any(entry.name != MARKER for entry in root.iterdir()):
                    raise ValueError("该目录含有已有项目资料；请恢复原项目数据库或选择其他位置")
                identifier = UUID(key)
            else:
                identifier = uuid4()
            self.files.bind(str(identifier), name, new_root=root)
            return self._create_at(identifier, name, root)

    def _create_at(self, identifier, name, root):

        def initialize(session, project_id):
            state = _new_state(project_id, name)
            session.add(
                HandoffProjectRow(
                    project_id=project_id, root=str(root), document=json.dumps(state), version=0
                )
            )

        self.projects.create(name, identifier=identifier, initialize=initialize)
        return self.get(str(identifier))

    def bind(self, key, *, relocate=False):
        project = self.projects.get(UUID(key))
        with self._lock(key):
            with self.sessions() as session:
                found = session.get(HandoffProjectRow, key) is not None
            if found and not relocate:
                return self.get(key)
            if relocate and not found:
                raise LookupError("请先为该项目创建目录")
            picked = self.files.picker.choose_directory() if relocate else None
            root = self.files.bind(key, project.name, existing_root=picked)
            with self.gate, self.sessions.begin() as session:
                if found:
                    row = self._row(session, key)
                    row.root, row.version = str(root), row.version + 1
                else:
                    session.add(
                        HandoffProjectRow(
                            project_id=key,
                            root=str(root),
                            document=json.dumps(_new_state(key, project.name)),
                            version=0,
                        )
                    )
            return self.get(key)

    def get(self, key, *, for_export=False):
        with self._lock(key), self.gate, self.sessions.begin() as session:
            row = self._row(session, key)
            state = json.loads(row.document)
            revision, graph = active_graph(self.graphs, session, key)
            if reconcile_graph(state, revision, graph):
                self._store(row, state)
            return _view(state, row.version, for_export=for_export)

    def scan(self, key):
        with self._lock(key):
            with self.sessions() as session:
                row = self._row(session, key)
                root, state = Path(row.root), json.loads(row.document)
            try:
                self.files.scan(root, state)
                state.pop("directory_error", None)
            except (ValueError, OSError) as exc:
                state["directory_error"] = str(exc)
            with self.gate, self.sessions.begin() as session:
                return self._store(self._row(session, key), state)

    def directory(self, key, data):
        with self.sessions() as session:
            row = self._row(session, key)
            root, state = Path(row.root), json.loads(row.document)
        if data.get("file_id"):
            item = _item(files_in(state), data["file_id"])
            try:
                self.files.verify_root(root, key)
                current = self.files.safe_path(root, item["path"])
                if _matches_file(current, item):
                    return current
            except (ValueError, OSError):
                pass
            # Upload dialogs need a filename with an extension. Keep editable copies separate from stored blobs.
            directory = self.settings.data_dir / "handoff-references" / item["sha256"]
            path = self.files.safe_path(directory, sanitize_filename(item["name"]))
            directory.mkdir(parents=True, exist_ok=True)
            if not _matches_file(path, item):
                fd, temp_name = tempfile.mkstemp(dir=directory)
                try:
                    with (
                        os.fdopen(fd, "wb") as dest,
                        self.files.blob_path(item["sha256"]).open("rb") as source,
                    ):
                        shutil.copyfileobj(source, dest)
                    os.replace(temp_name, path)
                finally:
                    Path(temp_name).unlink(missing_ok=True)
            return path
        self.files.verify_root(root, key)
        if data.get("group_id"):
            group = _item(state["groups"], data["group_id"])
            parts = {"group": "", "results": RESULTS, "refs": REFS}
            location = data.get("location") or "group"
            if location not in parts:
                raise ValueError("未知目录类型")
            rel = "/".join(filter(None, (group["path"], parts[location])))
        elif data.get("asset_id"):
            rel = _item(state["assets"], data["asset_id"])["path"]
        else:
            rel = ""
        path = self.files.safe_path(root, rel)
        if not path.exists():
            raise FileNotFoundError("原目录或文件已移动")
        return path

    def locate(self, key, query):
        query = query.strip().strip('"').replace("\\", "/")
        with self.sessions() as session:
            row = self._row(session, key)
            state, root = json.loads(row.document), Path(row.root)
        if not query:
            return []
        needle = os.path.normcase(query)
        results = []
        visible = {s["id"] for s in state["shots"]}
        for group in state["groups"]:
            if group["shot_id"] not in visible:
                continue
            for candidate in group["candidates"]:
                paths = [candidate["path"], (root / candidate["path"]).as_posix()]
                if (
                    query.lower() == candidate["sha256"]
                    or needle in map(os.path.normcase, paths)
                    or ("/" not in query and query.casefold() in candidate["name"].casefold())
                ):
                    results.append(dict(group_id=group["id"], candidate_id=candidate["id"]))
        return results

    def media(self, key, digest):
        with self.sessions() as session:
            state = json.loads(self._row(session, key).document)
        item = next((f for f in files_in(state) if f["sha256"] == digest), None)
        if item is None:
            raise LookupError("该项目没有这个素材版本")
        return item, self.files.blob_path(digest)

    def act(self, key, action, data, expected_version=None):
        if action == "scan":
            return self.scan(key)
        if action == "open_directory":
            path = self.directory(key, data)
            path = path if path.is_dir() else path.parent
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
            return self.get(key)
        with self._lock(key), self.gate, self.sessions.begin() as session:
            row = self._row(session, key)
            root, state = Path(row.root), json.loads(row.document)
            revision, graph = active_graph(self.graphs, session, key)
            changed_graph = reconcile_graph(state, revision, graph)
            if expected_version is not None and (expected_version != row.version or changed_graph):
                raise HandoffConflict("项目已更新，请刷新后重试")
            previous = None
            if action == "add_episode":
                index = len(state["episodes"]) + 1
                state["episodes"].append(
                    dict(id=str(uuid4()), title=_title(data, f"第 {index} 集"), path=f"EP{index:02}")
                )
            elif action == "add_shot":
                episode = _item(state["episodes"], data.get("episode_id"))
                number = self._shot_number(state, episode["id"])
                shot = shot_record(
                    str(uuid4()), episode["id"], _title(data, f"镜头 {number}"), len(state["shots"])
                )
                shot["path"] = f"{episode['path']}/SH{number:02}"
                self.files.verify_root(root, key)
                self.files.safe_path(root, shot["path"]).mkdir(parents=True, exist_ok=True)
                state["shots"].append(shot)
            elif action in {"add_group", "copy_group"}:
                self.files.verify_root(root, key)
                source = _item(state["groups"], data.get("group_id")) if action == "copy_group" else None
                shot = _item(state["shots"], source["shot_id"] if source else data.get("shot_id"))
                if "path" not in shot:
                    episode = _item(state["episodes"], shot["episode_id"])
                    shot["path"] = f"{episode['path']}/SH{self._shot_number(state, episode['id']):02}"
                index = 1 + sum(g["shot_id"] == shot["id"] for g in state["groups"])
                group = dict(
                    id=str(uuid4()),
                    shot_id=shot["id"],
                    title=_title(data, f"第 {index} 组"),
                    path=f"{shot['path']}/G{index:02}",
                    conditions=[],
                    candidates=[],
                    references=[],
                    bindings=[],
                    draft_prompt="",
                    draft_config="",
                )
                if source:
                    self.files.copy_group(root, source, group)
                else:
                    self.files.ensure_group(root, group["path"])
                state["groups"].append(group)
            elif action == "add_asset":
                self.files.verify_root(root, key)
                kind = data.get("kind")
                category = next((name for name, value in SHARED.items() if value == kind), None)
                if category is None:
                    raise ValueError("未知素材类别")
                name = _title({"title": data.get("name")}, "")
                if sanitize_filename(name, max_length=80) != name:
                    raise ValueError("请使用可作为本地文件夹名的素材名称")
                path = f"共享素材/{category}/{name}"
                if any(os.path.normcase(a["path"]) == os.path.normcase(path) for a in state["assets"]):
                    raise ValueError("同类素材已经使用了这个名称")
                self.files.safe_path(root, path + "/V1").mkdir(parents=True, exist_ok=True)
                state["assets"].append(dict(id=str(uuid4()), kind=kind, name=name, path=path, versions=[]))
            else:
                if action == "select_candidate":
                    previous = _item(state["shots"], data.get("shot_id"))["selected_id"]
                apply_edit(state, action, data)
            if action in {"add_shot", "reorder_shots", "select_candidate"}:
                if action == "select_candidate" and data.get("candidate_id"):
                    take = _item((c for g in state["groups"] for c in g["candidates"]), data["candidate_id"])
                    register_take(session, self.settings, key, take, self.files.blob_path(take["sha256"]))
                reflect_edit(self.graphs, session, state, graph, action, data.get("shot_id"), previous)
            return self._store(row, state)

    @staticmethod
    def _shot_number(state, episode_id):
        allocated = [
            s.get("path", "").rsplit("/SH", 1)[-1]
            for s in state["shots"] + state.get("_detached", [])
            if s["episode_id"] == episode_id
        ]
        return max([int(n) for n in allocated if n.isdecimal()] or [0]) + 1
