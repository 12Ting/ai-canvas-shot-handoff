"""Keep the explicit shot choice and the existing canvas on the same revision."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import utc_now
from ai_canvas.config import Settings
from ai_canvas.contracts.graph import GraphSnapshotV3
from ai_canvas.domain.graph.snapshots import normalize_to_v3
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.blob_lifecycle_repository import BlobLifecycleRepository
from ai_canvas.infrastructure.db.models import ArtifactRevisionRow, ArtifactRow, StoredBlobRow


def shot_record(identifier: str, episode_id: str, title: str, order: int) -> dict:
    return dict(
        id=identifier,
        episode_id=episode_id,
        title=title,
        order=order,
        selected_id=None,
        dialogue="",
        sounds=[],
    )


def register_take(session: Session, settings: Settings, project_id: str, take: dict, source: Path) -> None:
    # One revision per candidate, even when two generating groups produced identical bytes.
    existing = session.get(ArtifactRevisionRow, take["id"])
    if existing is not None:
        if existing.source_project_id != project_id or existing.blob_sha256 != take["sha256"]:
            raise ValueError("素材版本与镜头记录不一致")
        return
    store = ArtifactStore(settings.data_dir, max_bytes=settings.media_single_file_max_bytes)
    digest = take["sha256"]
    blob = session.get(StoredBlobRow, digest)
    BlobLifecycleRepository.assert_ingest_reusable(blob, verified_new_bytes=True)
    with source.open("rb") as handle:
        staged = store.stage_chunks(
            uuid4(), iter(lambda: handle.read(1024 * 1024), b""), declared_size=source.stat().st_size
        )
    if staged.sha256 != digest:
        store.discard(staged)
        raise ValueError("素材内容与保存的版本不符")
    store.promote(staged)
    now = utc_now()
    if blob is None:
        session.add(
            StoredBlobRow(
                sha256=digest,
                byte_size=staged.byte_size,
                media_kind=take["kind"],
                media_type=take["mime"],
                metadata_json=json.dumps(take["metadata"]),
                created_at=now,
            )
        )
        session.flush()
    else:
        BlobLifecycleRepository().reactivate_verified_purged_blob(session, digest, verified_new_bytes=True)
    artifact_id = str(uuid4())
    session.add(ArtifactRow(id=artifact_id, origin_project_id=project_id, kind=take["kind"], created_at=now))
    session.flush()
    session.add(
        ArtifactRevisionRow(
            id=take["id"],
            artifact_id=artifact_id,
            sequence=1,
            blob_sha256=digest,
            source_kind="handoff_selection",
            source_project_id=project_id,
            display_filename=take["name"],
            created_at=now,
        )
    )
    session.flush()
    BlobLifecycleRepository().recompute_for_sha(session, digest)


def active_graph(service: GraphService, session: Session, project_id: str):
    document = service.repository.document_for_project(session, project_id)
    revision = service.repository.revision(session, document.active_revision_id)
    return revision.id, normalize_to_v3(service._load_stored_snapshot(revision))


def reconcile_graph(state: dict, revision_id: str, graph: GraphSnapshotV3) -> bool:
    if state.get("graph_revision") == revision_id:
        return False
    existing = {s["id"]: s for s in state["shots"] + state.pop("_detached", [])}
    takes_by_shot = {}
    for group in state["groups"]:
        takes_by_shot.setdefault(group["shot_id"], set()).update(c["id"] for c in group["candidates"])
    visible = []
    for index, value in enumerate(sorted(graph.shots, key=lambda s: s.order)):
        key = str(value.id)
        takes = takes_by_shot.get(key, set())
        shot = existing.pop(key, None)
        if shot is None:
            shot = shot_record(key, state["episodes"][0]["id"], f"镜头 {index + 1}", index)
        original = shot["selected_id"]
        slots = [str(x) for x in (value.video_revision_id, value.image_revision_id) if x]
        shot["selected_id"] = original if original in slots else next((v for v in slots if v in takes), None)
        video_id = str(value.video_revision_id) if value.video_revision_id else None
        shot["legacy_video_revision_id"] = video_id if video_id and video_id not in takes else None
        shot["order"] = index
        visible.append(shot)
    state.update(shots=visible, _detached=list(existing.values()), graph_revision=revision_id)
    return True


def reflect_edit(
    service: GraphService,
    session: Session,
    state: dict,
    graph: GraphSnapshotV3,
    action: str,
    shot_id: str | None,
    previous_choice: str | None,
) -> None:
    payload = graph.model_dump(mode="json")
    slots = {s["id"]: s for s in payload["shots"]}
    episode_order = {ep["id"]: index for index, ep in enumerate(state["episodes"])}
    ordered = sorted(state["shots"], key=lambda s: (episode_order[s["episode_id"]], s["order"]))
    for index, shot in enumerate(ordered):
        key = shot["id"]
        if key not in slots:
            slot = {"id": key, "order": index}
            payload["shots"].append(slot)
            slots[key] = slot
            payload["nodes"].append(
                dict(
                    id=str(uuid4()),
                    kind="shot",
                    data={"shot_id": key},
                    position={"x": (index % 5) * 270, "y": (index // 5) * 180},
                )
            )
        slots[key]["order"] = index
        if action == "select_candidate" and key == shot_id:
            slot = slots[key]
            for field in ("video_revision_id", "image_revision_id"):
                if previous_choice and slot.get(field) == previous_choice:
                    slot[field] = None
            if shot["selected_id"]:
                candidate = next(
                    c for g in state["groups"] for c in g["candidates"] if c["id"] == shot["selected_id"]
                )
                slot[candidate["kind"] + "_revision_id"] = candidate["id"]
    revision = service.create_revision_for_approved_change_set_in_session(
        session,
        UUID(state["project_id"]),
        base_revision_id=UUID(state["graph_revision"]),
        client_mutation_id=uuid4(),
        snapshot=GraphSnapshotV3.model_validate(payload),
    )
    reconcile_graph(state, str(revision.revision_id), normalize_to_v3(revision.snapshot))
