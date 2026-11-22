import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from ai_canvas.application.graph_service import GraphService, canonical_snapshot
from ai_canvas.config import Settings
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.contracts.graph import (
    GraphSnapshotV2,
    GraphSnapshotV3,
    Point,
    Shot,
    ShotNodeData,
    ShotNodeV2,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphRevisionRow,
    StoredBlobRow,
)
from ai_canvas.main import create_app


def _authorized_headers(app, client: TestClient) -> dict[str, str]:
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert exchanged.status_code == 200
    return {
        "Authorization": f"Bearer {exchanged.json()['access_token']}",
        "Origin": "http://testserver",
    }


def _add_route_artifact_revision(
    app,
    project_id: str,
    kind: str,
    *,
    display_filename: str,
) -> str:
    revision_id = uuid4()
    artifact_id = uuid4()
    digest = hashlib.sha256(str(revision_id).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    metadata_json = (
        '{"kind":"image","width":8,"height":6}'
        if kind == "image"
        else (
            '{"kind":"video","width":8,"height":6,'
            '"duration_seconds":1.0,"frames_per_second":24.0}'
        )
    )
    with app.state.graph_service.session_factory.begin() as session:
        session.add(
            StoredBlobRow(
                sha256=digest,
                byte_size=16,
                media_kind=kind,
                media_type="image/png" if kind == "image" else "video/mp4",
                metadata_json=metadata_json,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRow(
                id=str(artifact_id),
                origin_project_id=project_id,
                kind=kind,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRevisionRow(
                id=str(revision_id),
                artifact_id=str(artifact_id),
                sequence=1,
                blob_sha256=digest,
                source_kind="fixture",
                source_project_id=project_id,
                source_import_session_id=None,
                display_filename=display_filename,
                created_at=now,
            )
        )
    return str(revision_id)


def _add_route_image_artifact_revisions(
    app,
    project_id: str,
    *,
    count: int,
    display_prefix: str,
) -> list[str]:
    revision_ids = [uuid4() for _ in range(count)]
    now = datetime.now(timezone.utc)
    blobs: list[StoredBlobRow] = []
    artifacts: list[ArtifactRow] = []
    revisions: list[ArtifactRevisionRow] = []
    for index, revision_id in enumerate(revision_ids):
        artifact_id = uuid4()
        digest = hashlib.sha256(str(revision_id).encode()).hexdigest()
        blobs.append(
            StoredBlobRow(
                sha256=digest,
                byte_size=16,
                media_kind="image",
                media_type="image/png",
                metadata_json='{"kind":"image","width":8,"height":6}',
                created_at=now,
            )
        )
        artifacts.append(
            ArtifactRow(
                id=str(artifact_id),
                origin_project_id=project_id,
                kind="image",
                created_at=now,
            )
        )
        revisions.append(
            ArtifactRevisionRow(
                id=str(revision_id),
                artifact_id=str(artifact_id),
                sequence=1,
                blob_sha256=digest,
                source_kind="fixture",
                source_project_id=project_id,
                source_import_session_id=None,
                display_filename=f"{display_prefix}-{index:02d}.png",
                created_at=now,
            )
        )
    with app.state.graph_service.session_factory.begin() as session:
        session.add_all(blobs)
        session.flush()
        session.add_all(artifacts)
        session.flush()
        session.add_all(revisions)
    return [str(revision_id) for revision_id in revision_ids]


def _shot_snapshot(
    shot_id: str,
    shot_node_id: str,
    *,
    image_revision_id: str | None = None,
    video_revision_id: str | None = None,
    reference_revision_ids: list[str] | None = None,
    marker: str | None = None,
) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {
            "id": shot_node_id,
            "kind": "shot",
            "position": {"x": 10, "y": 20},
            "data": {"shot_id": shot_id},
        }
    ]
    if marker is not None:
        nodes.append(
            {
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "kind": "note",
                "position": {"x": 30, "y": 40},
                "data": {"text": marker},
            }
        )
    return {
        "schema_version": 2,
        "nodes": nodes,
        "edges": [],
        "shots": [
            {
                "id": shot_id,
                "order": 0,
                "image_revision_id": image_revision_id,
                "video_revision_id": video_revision_id,
                "reference_revision_ids": reference_revision_ids or [],
            }
        ],
    }


def _save_route_snapshot(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    base_revision_id: str,
    snapshot: dict[str, object],
) -> dict[str, object]:
    del headers
    request = CreateGraphChangeSetRequest.model_validate(
        {
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": base_revision_id,
            "client_mutation_id": str(uuid4()),
            "operations": [{"op": "replace_snapshot", "snapshot": snapshot}],
        }
    )
    proposal = client.app.state.graph_changeset_service.request(
        UUID(project_id),
        request,
        actor_id="route-history-fixture",
    )
    client.app.state.graph_changeset_service.approve(
        UUID(project_id),
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="route-history-fixture",
    )
    applied = client.app.state.graph_changeset_service.apply(
        UUID(project_id),
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="route-history-fixture",
    )
    assert applied.applied_graph_revision_id is not None
    graph = client.app.state.graph_service.get_active(UUID(project_id))
    assert graph.revision_id == applied.applied_graph_revision_id
    return graph.model_dump(mode="json")


def _request_route_changeset(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    base_revision_id: str,
    snapshot: dict[str, object],
):
    return client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets",
        json={
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": base_revision_id,
            "client_mutation_id": str(uuid4()),
            "operations": [{"op": "replace_snapshot", "snapshot": snapshot}],
        },
        headers=headers,
    )


def _tamper_cursor_payload(cursor: str, **changes: object) -> str:
    encoded_payload, signature = cursor.split(".")
    padding = "=" * (-len(encoded_payload) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
    )
    payload.update(changes)
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return f"{tampered_payload}.{signature}"


def _resign_cursor_payload(
    cursor: str,
    known_secret: str,
    *,
    remove_fields: tuple[str, ...] = (),
    **changes: object,
) -> str:
    encoded_payload, _signature = cursor.split(".")
    padding = "=" * (-len(encoded_payload) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
    )
    for field in remove_fields:
        payload.pop(field, None)
    payload.update(changes)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    resigned_payload = (
        base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    )
    resigned_signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                known_secret.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{resigned_payload}.{resigned_signature}"


def test_graph_read_and_changeset_route_contract(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        exchanged = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
                "Origin": "http://testserver",
            },
        )
        headers = {
            "Authorization": f"Bearer {exchanged.json()['access_token']}",
            "Origin": "http://testserver",
        }
        created = client.post("/api/v1/projects", json={"name": "Route Demo"}, headers=headers)
        assert created.status_code == 201
        project = created.json()
        assert client.get(f"/api/v1/projects/{project['id']}", headers=headers).json() == project
        assert [item["id"] for item in client.get("/api/v1/projects", headers=headers).json()] == [
            project["id"]
        ]

        empty = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        assert empty["snapshot"] == {
            "schema_version": 2,
            "nodes": [],
            "edges": [],
            "shots": [],
        }
        snapshot = {
            "schema_version": 2,
            "nodes": [{
                "id": str(uuid4()),
                "kind": "note",
                "position": {"x": 10, "y": 20},
                "data": {"text": "hello"},
            }],
            "edges": [],
            "shots": [],
        }
        legacy = client.post(
            f"/api/v1/projects/{project['id']}/graph-revisions",
            json={
                "base_revision_id": empty["revision_id"],
                "client_mutation_id": str(uuid4()),
                "snapshot": snapshot,
            },
            headers=headers,
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project["id"],
            empty["revision_id"],
            snapshot,
        )
        assert legacy.status_code == 404
        assert saved["snapshot"]["schema_version"] == 3
        assert saved["snapshot"]["shots"] == []

        stale = _request_route_changeset(
            client,
            headers,
            project["id"],
            empty["revision_id"],
            snapshot,
        )
        assert stale.status_code == 409
        assert stale.json() == {
            "code": "graph_base_revision_stale",
            "current_revision_id": saved["revision_id"],
        }

        viewport = {"x": 120, "y": -30, "zoom": 1.25}
        updated = client.put(
            f"/api/v1/projects/{project['id']}/view-state", json=viewport, headers=headers
        )
        after_view = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        assert updated.json() == viewport
        assert after_view["revision_id"] == saved["revision_id"]
        assert after_view["viewport"] == viewport

        target_id = str(uuid4())
        invalid_graph = _request_route_changeset(
            client,
            headers,
            project["id"],
            saved["revision_id"],
            {
                "schema_version": 1,
                "nodes": [{
                    "id": target_id,
                    "kind": "prompt",
                    "position": {"x": 0, "y": 0},
                    "data": {"text": ""},
                }],
                "edges": [{
                    "id": str(uuid4()),
                    "source": str(uuid4()),
                    "source_port": "text",
                    "target": target_id,
                    "target_port": "text",
                }],
            },
        )
        malformed = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets", json={}, headers=headers
        )
        v2_kind_in_v1 = _request_route_changeset(
            client,
            headers,
            project["id"],
            saved["revision_id"],
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": str(uuid4()),
                        "kind": "media_input",
                        "position": {"x": 0, "y": 0},
                        "data": {"artifact_revision_id": None, "media_kind": None},
                    }
                ],
                "edges": [],
            },
        )
        shot_id = str(uuid4())
        reference_id = str(uuid4())
        invalid_v2_identity = _request_route_changeset(
            client,
            headers,
            project["id"],
            saved["revision_id"],
            {
                "schema_version": 2,
                "nodes": [
                    {
                        "id": str(uuid4()),
                        "kind": "shot",
                        "position": {"x": 0, "y": 0},
                        "data": {"shot_id": shot_id},
                    }
                ],
                "edges": [],
                "shots": [
                    {
                        "id": shot_id,
                        "order": 0,
                        "image_revision_id": None,
                        "video_revision_id": None,
                        "reference_revision_ids": [reference_id, reference_id],
                    }
                ],
            },
        )
        v2_saved = _save_route_snapshot(
            client,
            headers,
            project["id"],
            saved["revision_id"],
            {
                "nodes": [{
                    "id": str(uuid4()),
                    "kind": "media_input",
                    "position": {"x": 0, "y": 0},
                    "data": {"artifact_revision_id": None, "media_kind": None},
                }],
                "edges": [],
                "shots": [],
                "schema_version": 2,
            },
        )
        assert invalid_graph.status_code == 422
        assert invalid_graph.json()["code"] == "invalid_graph"
        assert malformed.status_code == 422
        assert malformed.json()["code"] == "request_validation"
        assert v2_kind_in_v1.status_code == 422
        assert v2_kind_in_v1.json()["code"] == "request_validation"
        assert invalid_v2_identity.status_code == 422
        assert invalid_v2_identity.json()["code"] == "invalid_graph"
        assert v2_saved["snapshot"]["nodes"][0]["kind"] == "media_input"


def test_corrupt_stored_graph_returns_declared_structured_500(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        exchanged = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
                "Origin": "http://testserver",
            },
        )
        headers = {
            "Authorization": f"Bearer {exchanged.json()['access_token']}",
            "Origin": "http://testserver",
        }
        project = client.post(
            "/api/v1/projects", json={"name": "Integrity"}, headers=headers
        ).json()
        initial = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        saved = _save_route_snapshot(
            client,
            headers,
            project["id"],
            initial["revision_id"],
            {"schema_version": 2, "nodes": [], "edges": [], "shots": []},
        )
        with app.state.graph_service.session_factory.begin() as session:
            revision = session.get(GraphRevisionRow, saved["revision_id"])
            assert revision is not None
            revision.snapshot_hash = "0" * 64

        active_response = client.get(
            f"/api/v1/projects/{project['id']}/graph", headers=headers
        )
        replay_response = _request_route_changeset(
            client,
            headers,
            project["id"],
            saved["revision_id"],
            {"schema_version": 2, "nodes": [], "edges": [], "shots": []},
        )

        expected = {
            "code": "stored_graph_integrity",
            "revision_id": saved["revision_id"],
        }
        assert active_response.status_code == 500
        assert active_response.json() == expected
        assert replay_response.status_code == 500
        assert replay_response.json() == expected

        openapi = app.openapi()
        graph_path = openapi["paths"]["/api/v1/projects/{project_id}/graph"]
        changeset_path = openapi["paths"][
            "/api/v1/projects/{project_id}/graph-change-sets"
        ]
        assert "500" in graph_path["get"]["responses"]
        assert "500" in changeset_path["post"]["responses"]
        assert "/api/v1/projects/{project_id}/graph-revisions" not in openapi["paths"]
        v1_schema = openapi["components"]["schemas"]["GraphSnapshotV1"]
        assert v1_schema["properties"]["schema_version"]["default"] == 1
        assert "schema_version" not in v1_schema.get("required", [])


def test_shot_slot_association_history_reports_saved_slot_transitions(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Shot association history"},
            headers=headers,
        ).json()
        project_id = project["id"]
        active = client.get(
            f"/api/v1/projects/{project_id}/graph",
            headers=headers,
        ).json()
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        image_a = _add_route_artifact_revision(
            app,
            project_id,
            "image",
            display_filename="image-a.png",
        )
        image_b = _add_route_artifact_revision(
            app,
            project_id,
            "image",
            display_filename="image-b.png",
        )
        video_a = _add_route_artifact_revision(
            app,
            project_id,
            "video",
            display_filename="video-a.mp4",
        )
        video_b = _add_route_artifact_revision(
            app,
            project_id,
            "video",
            display_filename="video-b.mp4",
        )

        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            active["revision_id"],
            _shot_snapshot(shot_id, shot_node_id),
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
                video_revision_id=video_a,
                reference_revision_ids=[image_a],
            ),
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
                video_revision_id=video_a,
                reference_revision_ids=[image_a],
                marker="unrelated edit",
            ),
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
                video_revision_id=video_b,
                reference_revision_ids=[image_b, image_a],
                marker="replace image and video",
            ),
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
                video_revision_id=video_b,
                reference_revision_ids=[image_a, image_b],
                marker="reference reorder only",
            ),
        )
        saved = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                video_revision_id=video_b,
                reference_revision_ids=[image_b],
                marker="clear image and remove reference",
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project_id,
            saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
                video_revision_id=video_b,
                reference_revision_ids=[image_a, image_b],
                marker="associate image and reference again",
            ),
        )

        def history(slot: str) -> dict[str, object]:
            response = client.get(
                f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/{slot}/associations",
                params={"anchor_revision_id": anchor["revision_id"], "limit": 50},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        image_history = history("image")
        assert image_history["project_id"] == project_id
        assert image_history["shot_id"] == shot_id
        assert image_history["slot"] == "image"
        assert image_history["anchor_graph_revision_id"] == anchor["revision_id"]
        assert image_history["anchor_graph_sequence"] == 7
        assert image_history["next_cursor"] is None
        assert [
            (
                item["graph_revision_sequence"],
                item["event_ordinal"],
                item["action"],
                (
                    item["artifact_revision"]["revision_id"]
                    if item["artifact_revision"] is not None
                    else None
                ),
            )
            for item in image_history["items"]
        ] == [
            (7, 0, "associated", image_a),
            (6, 0, "cleared", None),
            (4, 0, "associated", image_b),
            (2, 0, "associated", image_a),
        ]

        video_history = history("video")
        assert [
            (
                item["graph_revision_sequence"],
                item["action"],
                item["artifact_revision"]["revision_id"],
            )
            for item in video_history["items"]
        ] == [
            (4, "associated", video_b),
            (2, "associated", video_a),
        ]

        reference_history = history("reference")
        assert [
            (
                item["graph_revision_sequence"],
                item["event_ordinal"],
                item["action"],
                item["reference_index"],
                item["artifact_revision"]["revision_id"],
            )
            for item in reference_history["items"]
        ] == [
            (7, 0, "associated", 0, image_a),
            (6, 0, "removed", 0, image_a),
            (4, 0, "associated", 0, image_b),
            (2, 0, "associated", 0, image_a),
        ]
        assert all(
            item["graph_revision_sequence"] != 5
            for item in reference_history["items"]
        )

        safe_revision_keys = {
            "revision_id",
            "artifact_id",
            "sequence",
            "media_kind",
            "media_type",
            "byte_size",
            "display_filename",
            "metadata",
            "source_kind",
            "source_project_id",
            "origin_project_id",
            "created_at",
        }
        for response in (image_history, video_history, reference_history):
            for item in response["items"]:
                assert set(item) == {
                    "graph_revision_id",
                    "graph_revision_sequence",
                    "event_ordinal",
                    "occurred_at",
                    "action",
                    "reference_index",
                    "artifact_revision",
                }
                assert item["occurred_at"].endswith("Z")
                revision = item["artifact_revision"]
                if revision is not None:
                    assert set(revision) == safe_revision_keys
        serialized = str((image_history, video_history, reference_history)).lower()
        assert "snapshot_json" not in serialized
        assert "blob_sha256" not in serialized
        assert "local_path" not in serialized
        assert "token" not in serialized


def test_shot_slot_association_history_cursor_is_anchor_stable_and_bound(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Cursor binding"},
            headers=headers,
        ).json()
        project_id = project["id"]
        active = client.get(
            f"/api/v1/projects/{project_id}/graph",
            headers=headers,
        ).json()
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        image_a = _add_route_artifact_revision(
            app, project_id, "image", display_filename="a.png"
        )
        image_b = _add_route_artifact_revision(
            app, project_id, "image", display_filename="b.png"
        )
        saved = active
        for revision_id in (image_a, image_b, image_a, image_b):
            saved = _save_route_snapshot(
                client,
                headers,
                project_id,
                saved["revision_id"],
                _shot_snapshot(
                    shot_id,
                    shot_node_id,
                    image_revision_id=revision_id,
                ),
            )
        anchor = saved
        first = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["items"][0]["artifact_revision"]["revision_id"] == image_b
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)

        newer = _save_route_snapshot(
            client,
            headers,
            project_id,
            anchor["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
                marker="new autosave after anchor",
            ),
        )
        observed = list(first.json()["items"])
        while cursor is not None:
            page = client.get(
                f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
                params={
                    "anchor_revision_id": anchor["revision_id"],
                    "limit": 1,
                    "cursor": cursor,
                },
                headers=headers,
            )
            assert page.status_code == 200, page.text
            observed.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]
        assert [
            item["artifact_revision"]["revision_id"] for item in observed
        ] == [image_b, image_a, image_b, image_a]
        assert {item["graph_revision_sequence"] for item in observed} == {1, 2, 3, 4}
        assert newer["sequence"] == 5

        mismatched_anchor = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": newer["revision_id"],
                "limit": 1,
                "cursor": first.json()["next_cursor"],
            },
            headers=headers,
        )
        mismatched_slot = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/video/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "limit": 1,
                "cursor": first.json()["next_cursor"],
            },
            headers=headers,
        )
        malformed_cursor = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "cursor": "not-an-opaque-cursor",
            },
            headers=headers,
        )
        for response in (mismatched_anchor, mismatched_slot, malformed_cursor):
            assert response.status_code == 422
            assert response.json()["code"] == "shot_slot_association_cursor_invalid"

        second_project = client.post(
            "/api/v1/projects",
            json={"name": "Other cursor project"},
            headers=headers,
        ).json()
        second_active = client.get(
            f"/api/v1/projects/{second_project['id']}/graph",
            headers=headers,
        ).json()
        second_anchor = _save_route_snapshot(
            client,
            headers,
            second_project["id"],
            second_active["revision_id"],
            _shot_snapshot(shot_id, str(uuid4())),
        )
        cross_project_cursor = client.get(
            f"/api/v1/projects/{second_project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": second_anchor["revision_id"],
                "cursor": first.json()["next_cursor"],
            },
            headers=headers,
        )
        assert cross_project_cursor.status_code == 422
        assert (
            cross_project_cursor.json()["code"]
            == "shot_slot_association_cursor_invalid"
        )

        anchor_from_other_project = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": second_anchor["revision_id"]},
            headers=headers,
        )
        assert anchor_from_other_project.status_code == 404
        assert anchor_from_other_project.json()["code"] == "graph_revision_not_found"

        missing_shot = client.get(
            f"/api/v1/projects/{project_id}/shots/{uuid4()}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert missing_shot.status_code == 404
        assert missing_shot.json()["code"] == "shot_not_found"

        unknown_project = client.get(
            f"/api/v1/projects/{uuid4()}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert unknown_project.status_code == 404
        assert unknown_project.json()["code"] == "project_not_found"

        no_auth = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
        )
        assert no_auth.status_code == 401
        for invalid_params in (
            {"anchor_revision_id": anchor["revision_id"], "limit": 0},
            {"anchor_revision_id": anchor["revision_id"], "limit": 51},
        ):
            invalid_limit = client.get(
                f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/image/associations",
                params=invalid_params,
                headers=headers,
            )
            assert invalid_limit.status_code == 422
            assert invalid_limit.json()["code"] == "request_validation"
        unsupported_slot = client.get(
            f"/api/v1/projects/{project_id}/shots/{shot_id}/slots/audio/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert unsupported_slot.status_code == 422
        assert unsupported_slot.json()["code"] == "request_validation"


def test_shot_slot_association_history_cursor_blocks_old_lifecycle_and_tampering(
    tmp_path: Path,
) -> None:
    known_bootstrap = "known-bootstrap-token-for-cursor-regression"
    known_cursor_secret = "known-history-cursor-secret-for-schema-regression"
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            bootstrap_token=known_bootstrap,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        app.state.graph_service = GraphService(
            app.state.graph_service.session_factory,
            cursor_secret=known_cursor_secret,
        )
        project = client.post(
            "/api/v1/projects",
            json={"name": "Signed history cursor"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image_a = _add_route_artifact_revision(
            app, project["id"], "image", display_filename="old-lifecycle.png"
        )
        image_b = _add_route_artifact_revision(
            app, project["id"], "image", display_filename="new-lifecycle.png"
        )
        image_c = _add_route_artifact_revision(
            app, project["id"], "image", display_filename="anchor.png"
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        old_lifecycle = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
            ),
        )
        deleted = _save_route_snapshot(
            client,
            headers,
            project["id"],
            old_lifecycle["revision_id"],
            {"schema_version": 2, "nodes": [], "edges": [], "shots": []},
        )
        recreated = _save_route_snapshot(
            client,
            headers,
            project["id"],
            deleted["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            recreated["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_c,
            ),
        )
        first_page = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first_page.status_code == 200, first_page.text
        cursor = first_page.json()["next_cursor"]
        assert isinstance(cursor, str)
        assert cursor.count(".") == 1
        encoded_cursor_payload, _cursor_signature = cursor.split(".")
        cursor_payload_padding = "=" * (-len(encoded_cursor_payload) % 4)
        cursor_payload = json.loads(
            base64.urlsafe_b64decode(
                encoded_cursor_payload + cursor_payload_padding
            ).decode("utf-8")
        )
        assert cursor_payload["version"] == 2
        assert cursor_payload["next_revision_id"] == recreated["revision_id"]
        assert (
            cursor_payload["boundary_child_revision_id"]
            == anchor["revision_id"]
        )

        legitimate = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "limit": 10,
                "cursor": cursor,
            },
            headers=headers,
        )
        assert legitimate.status_code == 200, legitimate.text
        assert [
            item["artifact_revision"]["revision_id"]
            for item in legitimate.json()["items"]
        ] == [image_b]
        assert legitimate.json()["next_cursor"] is None

        encoded_payload, signature = cursor.split(".")
        changed_signature = (
            ("A" if signature[0] != "A" else "B") + signature[1:]
        )
        tampered = (
            _tamper_cursor_payload(cursor, next_sequence=1),
            _tamper_cursor_payload(cursor, next_revision_id=str(uuid4())),
            _tamper_cursor_payload(
                cursor,
                boundary_child_revision_id=str(uuid4()),
            ),
            _tamper_cursor_payload(cursor, shot_id=str(uuid4())),
            _tamper_cursor_payload(cursor, slot="video"),
            f"{encoded_payload}.{changed_signature}",
            _resign_cursor_payload(
                cursor,
                known_bootstrap,
                next_sequence=1,
                event_ordinal=0,
            ),
            _resign_cursor_payload(
                cursor,
                known_cursor_secret,
                remove_fields=(
                    "next_revision_id",
                    "boundary_child_revision_id",
                ),
                version=1,
            ),
        )
        for candidate in tampered:
            response = client.get(
                f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
                params={
                    "anchor_revision_id": anchor["revision_id"],
                    "cursor": candidate,
                },
                headers=headers,
            )
            assert response.status_code == 422
            assert (
                response.json()["code"]
                == "shot_slot_association_cursor_invalid"
            )


def test_shot_slot_association_history_cursor_is_rejected_after_key_rotation(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "History cursor key rotation"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image_a = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="restart-a.png",
        )
        image_b = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="restart-b.png",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        first_saved = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            first_saved["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
            ),
        )
        first_page = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first_page.status_code == 200
        cursor = first_page.json()["next_cursor"]
        assert isinstance(cursor, str)

        app.state.graph_service = GraphService(
            app.state.graph_service.session_factory,
            cursor_secret=b"a-different-process-local-cursor-secret",
        )
        continued = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "limit": 1,
                "cursor": cursor,
            },
            headers=headers,
        )
        assert continued.status_code == 422
        assert (
            continued.json()["code"]
            == "shot_slot_association_cursor_invalid"
        )


def test_shot_slot_association_history_reports_integrity_when_cursor_boundary_is_reparented(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Reparented history cursor boundary"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image_a = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="boundary-a.png",
        )
        image_b = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="boundary-b.png",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        parent = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            parent["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
            ),
        )
        first = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["items"][0]["artifact_revision"]["revision_id"] == image_b
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)

        with app.state.graph_service.session_factory.begin() as session:
            anchor_row = session.get(GraphRevisionRow, anchor["revision_id"])
            assert anchor_row is not None
            assert anchor_row.parent_revision_id == parent["revision_id"]
            anchor_row.parent_revision_id = active["revision_id"]

        continued = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "cursor": cursor,
            },
            headers=headers,
        )
        assert continued.status_code == 500
        assert continued.json() == {
            "code": "stored_graph_integrity",
            "revision_id": anchor["revision_id"],
        }


def test_shot_slot_association_history_pages_inside_one_reference_revision(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Reference event cursor"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        references = [
            _add_route_artifact_revision(
                app,
                project["id"],
                "image",
                display_filename=f"reference-{index:02d}.png",
            )
            for index in range(64)
        ]
        shot_id = str(uuid4())
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                str(uuid4()),
                reference_revision_ids=references,
            ),
        )
        cursor: str | None = None
        observed: list[dict[str, object]] = []
        page_sizes: list[int] = []
        while True:
            params: dict[str, object] = {
                "anchor_revision_id": anchor["revision_id"],
                "limit": 20,
            }
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get(
                f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
                params=params,
                headers=headers,
            )
            assert response.status_code == 200, response.text
            page_sizes.append(len(response.json()["items"]))
            observed.extend(response.json()["items"])
            cursor = response.json()["next_cursor"]
            if cursor is None:
                break
        assert page_sizes == [20, 20, 20, 4]
        assert [item["event_ordinal"] for item in observed] == list(range(64))
        assert [item["reference_index"] for item in observed] == list(range(64))
        assert [
            item["artifact_revision"]["revision_id"] for item in observed
        ] == references


def test_shot_slot_association_history_pages_all_128_reference_diff_events(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Maximum reference diff page"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        old_references = _add_route_image_artifact_revisions(
            app,
            project["id"],
            count=64,
            display_prefix="old-reference",
        )
        new_references = _add_route_image_artifact_revisions(
            app,
            project["id"],
            count=64,
            display_prefix="new-reference",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        parent = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                reference_revision_ids=old_references,
            ),
        )
        transition = _save_route_snapshot(
            client,
            headers,
            project["id"],
            parent["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                reference_revision_ids=new_references,
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            transition["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                reference_revision_ids=new_references,
                marker="anchor-witness",
            ),
        )

        cursor: str | None = None
        page_sizes: list[int] = []
        observed: list[dict[str, object]] = []
        for _page_index in range(8):
            params: dict[str, object] = {
                "anchor_revision_id": anchor["revision_id"],
                "limit": 16,
            }
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get(
                f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
                params=params,
                headers=headers,
            )
            assert response.status_code == 200, response.text
            page_sizes.append(len(response.json()["items"]))
            observed.extend(response.json()["items"])
            cursor = response.json()["next_cursor"]
            assert isinstance(cursor, str)
            encoded_payload, _signature = cursor.split(".")
            padding = "=" * (-len(encoded_payload) % 4)
            cursor_payload = json.loads(
                base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
            )
            if _page_index < 7:
                assert cursor_payload["next_sequence"] == transition["sequence"]
                assert (
                    cursor_payload["next_revision_id"]
                    == transition["revision_id"]
                )
                assert (
                    cursor_payload["boundary_child_revision_id"]
                    == anchor["revision_id"]
                )
                assert cursor_payload["event_ordinal"] == (_page_index + 1) * 16
            else:
                assert cursor_payload["next_sequence"] == parent["sequence"]
                assert cursor_payload["next_revision_id"] == parent["revision_id"]
                assert (
                    cursor_payload["boundary_child_revision_id"]
                    == transition["revision_id"]
                )
                assert cursor_payload["event_ordinal"] == 0

        assert page_sizes == [16] * 8
        assert [item["event_ordinal"] for item in observed] == list(range(128))
        assert [item["action"] for item in observed] == (
            ["associated"] * 64 + ["removed"] * 64
        )
        assert [
            item["artifact_revision"]["revision_id"] for item in observed
        ] == new_references + old_references
        assert [item["reference_index"] for item in observed] == (
            list(range(64)) + list(range(64))
        )
        assert {
            item["graph_revision_sequence"] for item in observed
        } == {transition["sequence"]}
        assert len(
            {
                (
                    item["event_ordinal"],
                    item["action"],
                    item["artifact_revision"]["revision_id"],
                )
                for item in observed
            }
        ) == 128

        earlier = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "limit": 1,
                "cursor": cursor,
            },
            headers=headers,
        )
        assert earlier.status_code == 200, earlier.text
        assert earlier.json()["items"][0]["graph_revision_sequence"] == parent["sequence"]
        assert earlier.json()["items"][0]["event_ordinal"] == 0
        assert (
            earlier.json()["items"][0]["artifact_revision"]["revision_id"]
            == old_references[0]
        )


def test_shot_slot_association_history_stops_scanning_at_200_revisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        repository = app.state.graph_service.repository
        original_revision = repository.revision
        requested_revision_ids: list[str] = []

        def record_revision(session, revision_id: str):
            requested_revision_ids.append(revision_id)
            return original_revision(session, revision_id)

        monkeypatch.setattr(
            repository,
            "revision",
            record_revision,
        )
        project = client.post(
            "/api/v1/projects",
            json={"name": "Bounded association scan"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="old-image.png",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        saved = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image,
                marker="0",
            ),
        )
        for index in range(1, 205):
            saved = _save_route_snapshot(
                client,
                headers,
                project["id"],
                saved["revision_id"],
                _shot_snapshot(
                    shot_id,
                    shot_node_id,
                    image_revision_id=image,
                    marker=str(index),
                ),
            )
        requested_revision_ids.clear()
        first = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": saved["revision_id"]},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["items"] == []
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)
        encoded_payload, _signature = cursor.split(".")
        padding = "=" * (-len(encoded_payload) % 4)
        cursor_payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        )
        assert cursor_payload["version"] == 2
        assert cursor_payload["next_sequence"] == saved["sequence"] - 199
        assert isinstance(cursor_payload["next_revision_id"], str)
        assert isinstance(cursor_payload["boundary_child_revision_id"], str)
        first_page_query_count = len(requested_revision_ids)
        assert first_page_query_count <= 201
        second = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": saved["revision_id"],
                "cursor": cursor,
            },
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert [
            item["artifact_revision"]["revision_id"]
            for item in second.json()["items"]
        ] == [image]
        assert second.json()["next_cursor"] is None
        second_page_requests = requested_revision_ids[first_page_query_count:]
        assert 1 <= len(second_page_requests) <= 10
        assert requested_revision_ids


def test_shot_slot_association_history_reports_integrity_when_200_scan_cursor_row_is_deleted(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Missing history continuation"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="old-image.png",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image,
                marker="0",
            ),
        )
        for index in range(1, 205):
            anchor = _save_route_snapshot(
                client,
                headers,
                project["id"],
                anchor["revision_id"],
                _shot_snapshot(
                    shot_id,
                    shot_node_id,
                    image_revision_id=image,
                    marker=str(index),
                ),
            )

        first = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)
        encoded_payload, _signature = cursor.split(".")
        padding = "=" * (-len(encoded_payload) % 4)
        cursor_payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        )
        next_sequence = cursor_payload["next_sequence"]
        boundary_child_revision_id = cursor_payload[
            "boundary_child_revision_id"
        ]
        assert isinstance(next_sequence, int)
        assert isinstance(boundary_child_revision_id, str)

        repository = app.state.graph_service.repository
        with app.state.graph_service.session_factory.begin() as session:
            anchor_row = session.get(GraphRevisionRow, anchor["revision_id"])
            assert anchor_row is not None
            continuation_rows = repository.revisions_descending(
                session,
                anchor_row.graph_document_id,
                next_sequence,
                1,
            )
            child_rows = repository.revisions_descending(
                session,
                anchor_row.graph_document_id,
                next_sequence + 1,
                1,
            )
            assert continuation_rows[0].sequence == next_sequence
            assert child_rows[0].sequence == next_sequence + 1
            continuation = continuation_rows[0]
            child = child_rows[0]
            assert child.parent_revision_id == continuation.id
            child.parent_revision_id = continuation.parent_revision_id
            session.flush()
            session.delete(continuation)

        continued = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "cursor": cursor,
            },
            headers=headers,
        )
        assert continued.status_code == 500
        assert continued.json() == {
            "code": "stored_graph_integrity",
            "revision_id": boundary_child_revision_id,
        }


def test_shot_slot_association_history_reports_integrity_when_cursor_current_loses_shot(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Missing continuation shot"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image_a = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="image-a.png",
        )
        image_b = _add_route_artifact_revision(
            app,
            project["id"],
            "image",
            display_filename="image-b.png",
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        parent = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_a,
            ),
        )
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            parent["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image_b,
            ),
        )
        first = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)

        missing_shot_snapshot = GraphSnapshotV2(nodes=[], edges=[], shots=[])
        snapshot_json, snapshot_hash = canonical_snapshot(missing_shot_snapshot)
        with app.state.graph_service.session_factory.begin() as session:
            parent_row = session.get(GraphRevisionRow, parent["revision_id"])
            assert parent_row is not None
            parent_row.snapshot_json = snapshot_json
            parent_row.snapshot_hash = snapshot_hash

        continued = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "cursor": cursor,
            },
            headers=headers,
        )
        assert continued.status_code == 500
        assert continued.json() == {
            "code": "stored_graph_integrity",
            "revision_id": parent["revision_id"],
        }


def test_shot_slot_association_history_reports_integrity_when_cursor_anchor_loses_shot(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Missing cursor anchor shot"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        references = _add_route_image_artifact_revisions(
            app,
            project["id"],
            count=2,
            display_prefix="cursor-anchor-reference",
        )
        shot_id = str(uuid4())
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                str(uuid4()),
                reference_revision_ids=references,
            ),
        )
        first = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
            params={"anchor_revision_id": anchor["revision_id"], "limit": 1},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert isinstance(cursor, str)
        encoded_payload, _signature = cursor.split(".")
        padding = "=" * (-len(encoded_payload) % 4)
        cursor_payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        )
        assert cursor_payload["next_sequence"] == anchor["sequence"]
        assert cursor_payload["event_ordinal"] == 1

        missing_shot_snapshot = GraphSnapshotV2(nodes=[], edges=[], shots=[])
        snapshot_json, snapshot_hash = canonical_snapshot(missing_shot_snapshot)
        with app.state.graph_service.session_factory.begin() as session:
            anchor_row = session.get(GraphRevisionRow, anchor["revision_id"])
            assert anchor_row is not None
            anchor_row.snapshot_json = snapshot_json
            anchor_row.snapshot_hash = snapshot_hash

        without_cursor = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert without_cursor.status_code == 404
        assert without_cursor.json() == {
            "code": "shot_not_found",
            "shot_id": shot_id,
        }

        continued = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/reference/associations",
            params={
                "anchor_revision_id": anchor["revision_id"],
                "cursor": cursor,
            },
            headers=headers,
        )
        assert continued.status_code == 500
        assert continued.json() == {
            "code": "stored_graph_integrity",
            "revision_id": anchor["revision_id"],
        }


def test_shot_slot_association_history_rejects_stored_kind_mismatch(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "History integrity"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        image = _add_route_artifact_revision(
            app, project["id"], "image", display_filename="image.png"
        )
        video = _add_route_artifact_revision(
            app, project["id"], "video", display_filename="video.mp4"
        )
        shot_id = str(uuid4())
        shot_node_id = str(uuid4())
        anchor = _save_route_snapshot(
            client,
            headers,
            project["id"],
            active["revision_id"],
            _shot_snapshot(
                shot_id,
                shot_node_id,
                image_revision_id=image,
            ),
        )
        corrupted_snapshot = GraphSnapshotV2(
            nodes=[
                ShotNodeV2(
                    id=UUID(shot_node_id),
                    kind="shot",
                    position=Point(x=10, y=20),
                    data=ShotNodeData(shot_id=UUID(shot_id)),
                )
            ],
            edges=[],
            shots=[
                Shot(
                    id=UUID(shot_id),
                    order=0,
                    image_revision_id=UUID(video),
                )
            ],
        )
        snapshot_json, snapshot_hash = canonical_snapshot(corrupted_snapshot)
        with app.state.graph_service.session_factory.begin() as session:
            row = session.get(GraphRevisionRow, anchor["revision_id"])
            assert row is not None
            row.snapshot_json = snapshot_json
            row.snapshot_hash = snapshot_hash

        response = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert response.status_code == 500
        assert response.json() == {
            "code": "stored_graph_integrity",
            "revision_id": anchor["revision_id"],
        }

        valid_snapshot = GraphSnapshotV3.model_validate(anchor["snapshot"])
        valid_json, valid_hash = canonical_snapshot(valid_snapshot)
        with app.state.graph_service.session_factory.begin() as session:
            graph_row = session.get(GraphRevisionRow, anchor["revision_id"])
            revision_row = session.get(ArtifactRevisionRow, image)
            assert graph_row is not None
            assert revision_row is not None
            graph_row.snapshot_json = valid_json
            graph_row.snapshot_hash = valid_hash
            revision_row.discarded_at = datetime.now(timezone.utc)

        discarded = client.get(
            f"/api/v1/projects/{project['id']}/shots/{shot_id}/slots/image/associations",
            params={"anchor_revision_id": anchor["revision_id"]},
            headers=headers,
        )
        assert discarded.status_code == 500
        assert discarded.json() == {
            "code": "stored_graph_integrity",
            "revision_id": anchor["revision_id"],
        }
