"""Human decisions in the local shot workspace. Files and scans never imply approval."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


def _text(data: dict, key: str, *, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"缺少有效文字：{key}")
    return value


def item(rows: list, identity: str, label: str) -> dict:
    if isinstance(identity, str) and identity:
        for row in rows:
            if row["id"] == identity:
                return row
    raise ValueError(f"找不到{label}")


def candidate_entry(state: dict, identity: str) -> tuple[dict, dict]:
    for group in state["groups"]:
        for candidate in group["candidates"]:
            if candidate["id"] == identity:
                return group, candidate
    raise ValueError("找不到候选版本")


def episode_shots(state: dict, episode_id: str) -> list[dict]:
    return sorted((s for s in state["shots"] if s["episode_id"] == episode_id), key=lambda s: s["order"])


def neighbors(state: dict, shot: dict) -> tuple[dict | None, dict | None]:
    ordered = episode_shots(state, shot["episode_id"])
    idx = next(i for i, entry in enumerate(ordered) if entry["id"] == shot["id"])
    return ordered[idx - 1] if idx else None, ordered[idx + 1] if idx + 1 < len(ordered) else None


def _candidate_for_shot(state, shot_id, candidate_id):
    group, candidate = candidate_entry(state, candidate_id)
    if group["shot_id"] != shot_id:
        raise ValueError("候选版本属于其他镜头")
    return candidate


def _set_reason(candidate, data):
    reason = _text(data, "reason", allow_empty=True).strip()
    if len(reason) > 500:
        raise ValueError("采用理由最多 500 字")
    candidate["selection_reason"] = reason


def _save_selection_reason(state, data):
    shot = item(state["shots"], data.get("shot_id"), "镜头")
    candidate = _candidate_for_shot(state, shot["id"], data.get("candidate_id"))
    _set_reason(candidate, data)


def _select(state, data):
    shot = item(state["shots"], data.get("shot_id"), "镜头")
    if "candidate_id" not in data:
        raise ValueError("请选择候选版本或明确撤销采用")
    candidate_id = data["candidate_id"]
    if candidate_id is not None:
        candidate = _candidate_for_shot(state, shot["id"], candidate_id)
        if "reason" in data:
            _set_reason(candidate, data)
    elif "reason" in data:
        raise ValueError("采用理由需要指定候选版本")
    shot["selected_id"] = candidate_id


def _reorder(state, data):
    episode = item(state["episodes"], data.get("episode_id"), "集")
    shots = episode_shots(state, episode["id"])
    ids = data.get("shot_ids")
    if (not isinstance(ids, list) or not all(isinstance(v, str) for v in ids)
            or len(ids) != len(shots) or set(ids) != {s["id"] for s in shots}):
        raise ValueError("顺序必须包含本集的全部镜头，每镜一次")
    ordering = {identity: i for i, identity in enumerate(ids)}
    for shot in shots:
        shot["order"] = ordering[shot["id"]]


def _set_conditions(state, data):
    group = item(state["groups"], data.get("group_id"), "生成组")
    condition = item(group["conditions"], data.get("condition_id"), "本组条件版本")
    ids = data.get("candidate_ids")
    if (not isinstance(ids, list) or not ids or not all(isinstance(v, str) for v in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("请选择需要补记条件的本组候选")
    candidates = [item(group["candidates"], identity, "本组候选") for identity in ids]
    timestamp = _stamp()
    for candidate in candidates:
        previous = candidate.get("condition_id")
        if previous != condition["id"]:
            candidate.setdefault("condition_history", []).append({
                "previous_id": previous, "condition_id": condition["id"], "at": timestamp,
                "source": "human",
            })
            candidate["condition_id"] = condition["id"]


def _asset_pin(state, data):
    asset = item(state["assets"], data.get("asset_id"), "共享素材")
    if "version_id" not in data:
        raise ValueError("请选择素材版本或明确取消关联")
    version_id = data["version_id"]
    if version_id is not None:
        item(asset["versions"], version_id, "该素材的版本")
    return asset, version_id


def _bind_asset(state, data):
    from ai_canvas.application.handoff_files import record_condition

    group = item(state["groups"], data.get("group_id"), "生成组")
    asset, version_id = _asset_pin(state, data)
    if asset["kind"] not in {"character", "scene", "prop"}:
        raise ValueError("生成组引用支持角色、场景和道具")
    bindings = list(group["bindings"])
    existing = next((b for b in bindings if b["asset_id"] == asset["id"]), None)
    if (existing or {}).get("version_id") == version_id:
        return
    if existing:
        bindings.remove(existing)
    if version_id is not None:
        bindings.append({"asset_id": asset["id"], "version_id": version_id})
    group["bindings"] = bindings
    current = group["conditions"][-1] if group["conditions"] else {"prompt": "", "config": ""}
    record_condition(group, current["prompt"], current["config"])


def _set_sound(state, data):
    shot = item(state["shots"], data.get("shot_id"), "镜头")
    asset, version_id = _asset_pin(state, data)
    role = data.get("role")
    if asset["kind"] != "sound" or role not in ("temp", "final", "reference"):
        raise ValueError("请选择声音资源及有效的声音用途")
    if version_id is not None and item(asset["versions"], version_id, "音频版本")["kind"] != "audio":
        raise ValueError("文件不是音频")
    sounds = shot["sounds"]
    old = next((s for s in sounds if s["asset_id"] == asset["id"] and s["role"] == role), None)
    if (old or {}).get("version_id") == version_id:
        return
    shot.setdefault("sound_history", []).append({"at": _stamp(), "sounds": deepcopy(sounds)})
    if old:
        sounds.remove(old)
    if version_id is not None:
        sounds.append({"asset_id": asset["id"], "version_id": version_id, "role": role})


def _update_dialogue(shot, text, version_id):
    if shot.get("dialogue_version_id") == version_id and shot["dialogue"] == text:
        return
    shot.setdefault("dialogue_history", []).append({
        "version_id": shot.get("dialogue_version_id"), "text": shot["dialogue"], "at": _stamp(),
    })
    shot["dialogue"] = text
    shot["dialogue_version_id"] = version_id


def _set_dialogue(state, data):
    shot = item(state["shots"], data.get("shot_id"), "镜头")
    text = _text(data, "text", allow_empty=True)
    if text != shot["dialogue"]:
        _update_dialogue(shot, text, _new_id() if text else None)


def _link_dialogue(state, data):
    shot = item(state["shots"], data.get("shot_id"), "镜头")
    source = item(state["shots"], data.get("source_shot_id"), "对白来源镜头")
    if not source["dialogue"]:
        raise ValueError("来源镜头尚无台词")
    identity = source.get("dialogue_version_id") or _new_id()
    source["dialogue_version_id"] = identity
    _update_dialogue(shot, source["dialogue"], identity)


def _open_issue(state, data):
    group, candidate = candidate_entry(state, data.get("candidate_id"))
    shot = item(state["shots"], group["shot_id"], "镜头")
    note = _text(data, "note")
    context = {
        "dialogue": shot["dialogue"], "dialogue_version_id": shot.get("dialogue_version_id"),
        "sounds": deepcopy(shot["sounds"]),
    }
    for side, neighbor in zip(("previous", "next"), neighbors(state, shot)):
        chosen = neighbor.get("selected_id") if neighbor else None
        temporary = data.get(f"{side}_id")
        if temporary is not None:
            if not neighbor:
                raise ValueError("临时对照超出本集镜头范围")
            _candidate_for_shot(state, neighbor["id"], temporary)
        context[f"{side}_shot_id"] = neighbor["id"] if neighbor else None
        context[f"{side}_id"] = temporary or chosen
        if temporary or chosen:
            _, adjacent_candidate = candidate_entry(state, temporary or chosen)
            context[f"{side}_condition_id"] = adjacent_candidate.get("condition_id")
        context[f"{side}_temporary"] = temporary is not None and temporary != chosen
    state["issues"].append({
        "id": _new_id(), "shot_id": shot["id"], "episode_id": shot["episode_id"],
        "candidate_id": candidate["id"], "condition_id": candidate.get("condition_id"),
        "note": note.strip(), "status": "open", "repair_id": None, "context": context,
        "opened_at": _stamp(),
    })


def _resolve_issue(state, data):
    issue = item(state["issues"], data.get("issue_id"), "待补镜意见")
    repair = data.get("candidate_id")
    if issue["status"] == "resolved":
        if repair != issue["repair_id"]:
            raise ValueError("该问题已解决")
        return
    if repair is not None:
        _candidate_for_shot(state, issue["shot_id"], repair)
        if repair == issue["candidate_id"]:
            raise ValueError("请选择另一版作为修复结果，或留空")
    issue.update(status="resolved", repair_id=repair, resolved_at=_stamp())


_EDITS = {
    "select_candidate": _select, "save_selection_reason": _save_selection_reason,
    "reorder_shots": _reorder, "set_conditions": _set_conditions,
    "bind_asset": _bind_asset, "set_sound": _set_sound, "set_dialogue": _set_dialogue,
    "link_dialogue": _link_dialogue, "open_issue": _open_issue, "resolve_issue": _resolve_issue,
}


def apply_edit(state: dict, action: str, data: dict) -> None:
    handler = _EDITS.get(action)
    if handler is None:
        raise ValueError("未知的镜头操作")
    if not isinstance(data, dict):
        raise ValueError("操作资料格式无效")
    handler(state, data)


def asset_usage(state: dict, asset_id: str, version_id: str) -> list[dict]:
    asset = item(state["assets"], asset_id, "共享素材")
    item(asset["versions"], version_id, "素材版本")
    shots = {shot["id"]: shot for shot in state["shots"]}
    result = []
    pin = {"asset_id": asset_id, "version_id": version_id}
    for group in state["groups"]:
        shot = shots[group["shot_id"]]
        for condition in group["conditions"]:
            if pin not in condition["bindings"]:
                continue
            result.append({
                "episode_id": shot["episode_id"], "shot_id": shot["id"], "group_id": group["id"],
                "condition_id": condition["id"], "current": condition is group["conditions"][-1],
                "candidate_ids": [c["id"] for c in group["candidates"] if c.get("condition_id") == condition["id"]],
            })
    for shot in shots.values():
        for sound in shot["sounds"]:
            if sound["asset_id"] == asset_id and sound["version_id"] == version_id:
                result.append({"episode_id": shot["episode_id"], "shot_id": shot["id"], "role": sound["role"]})
    return result
