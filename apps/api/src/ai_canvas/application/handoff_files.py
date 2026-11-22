"""Synchronize the user's working directories into a single handoff project."""

from __future__ import annotations

import json
import shutil
import hashlib
import stat
import time
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from ai_canvas.application.handoff_snapshots import SnapshotStore, checked_path, read_small, signature
from ai_canvas.config import Settings
from ai_canvas.infrastructure.artifacts.store import ArtifactStoreError, sanitize_filename
from ai_canvas.infrastructure.media.native_picker import NativeMediaPicker

MARKER = ".ai-canvas-project.json"
INPUTS = ("提示词.txt", "生成配置.txt")
REFS = "参考素材"
RESULTS = "生成结果"
SHARED = {"角色": "character", "场景": "scene", "道具": "prop", "声音": "sound"}
MEDIA = {ext: kind for kind, extensions in {
    "image": (".png", ".jpg", ".jpeg", ".webp"), "video": (".mp4", ".mov", ".webm", ".mkv"),
    "audio": (".mp3", ".wav"),
}.items() for ext in extensions}
TEXT_LIMIT = 1024 * 1024


def _id() -> str:
    return str(uuid4())


def _folder(parent: Path, child: str) -> Path:
    path = checked_path(parent, child)
    path.mkdir(parents=True, exist_ok=True)
    return checked_path(parent, child)


def _list(directory: Path):
    if not directory.exists():
        return []
    return sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name))


def _checked_dirs(root: Path, directory: Path):
    for entry in _list(directory):
        if not entry.name.startswith("."):
            checked_path(root, entry.relative_to(root).as_posix())
            if entry.is_dir():
                yield entry


def _pinned(files: list) -> list:
    return [{k: v for k, v in f.items() if k not in {"available", "source_identity"}} for f in files]


def record_condition(group: dict, prompt: str, config: str):
    refs = _pinned(group["references"])
    bindings = deepcopy(group["bindings"])
    contents = {"prompt": prompt, "config": config, "references": refs, "bindings": bindings}
    previous = group["conditions"][-1] if group["conditions"] else None
    if previous and all(previous.get(k) == v for k, v in contents.items()):
        return
    if not previous and not any(contents.values()):
        return
    group["conditions"].append({"id": _id(), **contents})


class HandoffFiles:
    def __init__(self, data_dir: Path, picker: NativeMediaPicker, *, settings: Settings | None = None,
                 settle_seconds: float = 0.5):
        self.picker = picker
        config = settings or Settings()
        self.snapshots = SnapshotStore(data_dir, config)
        self.project_parent = config.handoff_parent
        self.settle_seconds = settle_seconds
        self._observations: dict[Path, tuple[tuple, float]] = {}
        self._cache: dict[Path, tuple[tuple, dict]] = {}

    def blob_path(self, sha256: str) -> Path:
        return self.snapshots.blob_path(sha256)

    safe_path = staticmethod(checked_path)

    def root_id(self, root: Path) -> str:
        path = checked_path(root, MARKER)
        body = json.loads(read_small(root, path, 8192))
        if not isinstance(body, dict) or body.get("schema") != 1 or not isinstance(body.get("project_id"), str):
            raise ValueError("所选目录缺少有效的项目标识")
        return str(UUID(body["project_id"]))

    def verify_root(self, root: Path, project_id: str):
        if self.root_id(root) != project_id:
            raise ValueError("请选择同一项目的根目录")

    def project_root(self, name: str) -> Path:
        selected = self.project_parent if self.project_parent is not None else Path(self.picker.choose_directory())
        checked_path(selected)
        if not selected.is_dir():
            raise ValueError("所选目录不可用")
        return checked_path(selected.resolve(strict=True), sanitize_filename(name, max_length=80))

    def bind(self, project_id: str, name: str, existing_root: Path | None = None, *, new_root=None) -> Path:
        if existing_root is not None:
            self.verify_root(existing_root, project_id)
            return existing_root
        root = new_root if new_root is not None else self.project_root(name)
        if root.exists():
            self.verify_root(root, project_id)
            return root
        root.mkdir(exist_ok=False)
        with checked_path(root, MARKER).open("x", encoding="utf-8") as out:
            json.dump({"schema": 1, "project_id": project_id}, out)
        return root

    def ensure_group(self, root: Path, group_path: str):
        if not group_path:
            raise ValueError("生成组必须有独立目录")
        group = _folder(root, group_path)
        for name in (REFS, RESULTS):
            _folder(group, name)
        for name in INPUTS:
            path = checked_path(group, name)
            try:
                with path.open("xb"):
                    pass
            except FileExistsError:
                if not path.is_file():
                    raise ValueError("生成资料位置已被目录占用") from None

    def copy_group(self, root: Path, source: dict, target: dict):
        """Populate a new group from the latest stable inputs, including pinned references."""
        warnings = {}
        if not self._scan_inputs(root, source, warnings):
            raise ValueError("参考素材仍在写入或生成资料暂时无法读取，请稍后复制")
        existing = source["conditions"][-1] if source["conditions"] else {}
        if target["path"] == source["path"]:
            raise ValueError("新组需要独立目录")
        self.ensure_group(root, target["path"])
        directory = checked_path(root, target["path"])
        for field, filename in zip(("prompt", "config"), INPUTS, strict=True):
            path = checked_path(directory, filename)
            data = existing.get(field, "").encode("utf-8")
            before = read_small(directory, path, TEXT_LIMIT)
            if before and data != before:
                raise ValueError("复制目标已有其他生成资料")
            if not before and data:
                path.write_bytes(data)
        target["bindings"] = deepcopy(source.get("bindings", []))
        for ref in existing.get("references", source.get("references", [])):
            dest = checked_path(directory, f"{REFS}/{sanitize_filename(ref['name'])}")
            if dest.exists():
                digest = hashlib.sha256()
                with dest.open("rb") as current:
                    for chunk in iter(lambda: current.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != ref["sha256"]:
                    raise ValueError("复制目标已有其他参考素材")
                continue
            with self.blob_path(ref["sha256"]).open("rb") as inp, dest.open("xb") as out:
                shutil.copyfileobj(inp, out)

    def _capture(self, root: Path, path: Path, kind: str) -> dict | None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("需要普通文件")
        stamp = signature(info)
        cached = self._cache.get(path)
        if cached and cached[0] == stamp and self.snapshots.store.blob_path(cached[1]["sha256"]).is_file():
            return cached[1]
        seen = self._observations.get(path)
        if seen is None or seen[0] != stamp:
            self._observations[path] = (stamp, time.monotonic())
            return None
        if time.monotonic() - seen[1] < self.settle_seconds:
            return None
        if info.st_size == 0:
            raise ValueError("空媒体文件，等待下载完成")
        frozen = self.snapshots.capture(root, path, kind, info)
        frozen["source_identity"] = f"{info.st_dev}:{info.st_ino}" if info.st_ino else None
        self._cache[path] = (stamp, frozen)
        return frozen

    def _update_files(self, root: Path, directory: Path, records: list, allowed: set, warnings: dict,
                      *, keep_history: bool, condition_id: str | None = None, label: str | None = None):
        pending = False
        paths = _list(directory)
        names = {p.relative_to(root).as_posix() for p in paths}
        in_scope = [f for f in records if Path(f["path"]).parent.as_posix() ==
                    directory.relative_to(root).as_posix()]
        for previous in in_scope:
            if previous["path"] not in names:
                previous["available"] = False
        for path in paths:
            rel = path.relative_to(root).as_posix()
            suffix = path.suffix.lower()
            if path.name.startswith((".", "~")) or suffix in {".part", ".crdownload", ".tmp", ".download"}:
                continue
            try:
                checked_path(root, rel)
                if path.is_dir():
                    continue
                kind = MEDIA.get(suffix)
                if kind not in allowed:
                    warnings[rel] = "文件格式或用途不受支持"
                    continue
                frozen = self._capture(root, path, kind)
                if frozen is None:
                    pending = True
                    continue
                source_identity = frozen["source_identity"]
            except (ArtifactStoreError, OSError, ValueError, TypeError, KeyError) as exc:
                warnings[rel] = str(exc) or "暂时无法读取文件"
                continue
            same_path = [f for f in in_scope if f["path"] == rel]
            active = next((f for f in reversed(same_path) if f.get("available", True)), None)
            latest = same_path[-1] if same_path else None
            if latest and latest["sha256"] == frozen["sha256"]:
                latest.update(available=True, source_identity=source_identity)
                continue
            if active:
                active["available"] = False
            # Preserve ID on an unambiguous in-directory rename. Byte-identical
            # files intentionally present twice or in another group remain independent.
            lost = [f for f in in_scope if f["path"] not in names and f["sha256"] == frozen["sha256"]
                    and source_identity and f.get("source_identity") == source_identity]
            if len(lost) == 1:
                lost[0].update(path=rel, name=path.name, available=True)
                continue
            record = {"id": _id(), "name": path.name, "path": rel, **frozen, "available": True,
                      "version": max((f.get("version", 0) for f in same_path), default=0) + 1}
            if label is not None:
                record["version_label"] = label
            if condition_id is not None or directory.name == RESULTS:
                record["condition_id"] = condition_id
            records.append(record)
        if not keep_history:
            records[:] = [f for f in records if f.get("available", True)]
        return pending

    def _scan_inputs(self, root: Path, group: dict, warnings: dict) -> bool:
        base = checked_path(root, group["path"])
        if not base.is_dir():
            raise FileNotFoundError("生成组目录缺失")
        refdir = checked_path(base, REFS)
        pending = self._update_files(root, refdir, group["references"], {"image", "video"}, warnings,
                                     keep_history=False)
        last = group["conditions"][-1] if group["conditions"] else {}
        values = []
        for filename, key in zip(INPUTS, ("prompt", "config"), strict=True):
            path = checked_path(base, filename)
            try:
                values.append(read_small(base, path, TEXT_LIMIT).decode("utf-8-sig"))
            except (ValueError, OSError) as exc:
                warnings[path.relative_to(root).as_posix()] = str(exc)
                values.append(last.get(key, ""))
        group["draft_prompt"], group["draft_config"] = values
        complete = not pending and not any(p.startswith(group["path"] + "/") for p in warnings)
        if complete:
            record_condition(group, *values)
        return complete

    def _scan_group(self, root: Path, group: dict, warnings: dict):
        complete = self._scan_inputs(root, group, warnings)
        base = checked_path(root, group["path"])
        # A download can finish after the user edits the inputs. Multiple
        # observed recipes leave the candidate unassigned until explicit selection.
        latest = group["conditions"][0]["id"] if complete and len(group["conditions"]) == 1 else None
        self._update_files(root, checked_path(base, RESULTS), group["candidates"], {"image", "video"},
                           warnings, keep_history=True, condition_id=latest)

    def _scan_assets(self, root: Path, assets: list, warnings: dict):
        found = set()
        for folder, kind in SHARED.items():
            base = checked_path(root, f"共享素材/{folder}")
            for assetdir in _checked_dirs(root, base):
                rel = assetdir.relative_to(root).as_posix()
                asset = next((a for a in assets if a["path"] == rel), None)
                if asset is None:
                    asset = {"id": _id(), "kind": kind, "name": assetdir.name, "path": rel, "versions": []}
                    assets.append(asset)
                for ver in _checked_dirs(root, assetdir):
                    found.add(ver.relative_to(root).as_posix())
                    self._update_files(root, ver, asset["versions"],
                                       {"audio"} if kind == "sound" else {"image", "video"},
                                       warnings, keep_history=True, label=ver.name)
        for asset in assets:
            for f in asset["versions"]:
                if Path(f["path"]).parent.as_posix() not in found:
                    f["available"] = False

    def scan(self, root: Path, state: dict) -> bool:
        self.verify_root(root, state["project_id"])
        before = deepcopy((state["groups"], state["assets"], state.get("scan_warnings", {})))
        warnings: dict[str, str] = {}
        try:
            self._scan_assets(root, state["assets"], warnings)
        except (ValueError, OSError) as exc:
            warnings["共享素材"] = str(exc)
        for group in state["groups"]:
            try:
                self._scan_group(root, group, warnings)
            except (ValueError, OSError) as exc:
                warnings[group["path"]] = str(exc)
        state["scan_warnings"] = warnings
        return before != (state["groups"], state["assets"], warnings)
