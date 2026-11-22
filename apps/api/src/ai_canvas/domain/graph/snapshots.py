import hashlib
import json

from pydantic import TypeAdapter

from ai_canvas.contracts.graph import (
    GraphSnapshot,
    GraphSnapshotV1,
    GraphSnapshotV2,
    GraphSnapshotV3,
)
from ai_canvas.domain.prompting.document import document_from_text


_SNAPSHOT_ADAPTER = TypeAdapter(GraphSnapshot)


def parse_snapshot_json(
    snapshot_json: str,
) -> GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3:
    return _SNAPSHOT_ADAPTER.validate_json(snapshot_json)


def normalize_to_v2(snapshot: GraphSnapshotV1 | GraphSnapshotV2) -> GraphSnapshotV2:
    if isinstance(snapshot, GraphSnapshotV2):
        return GraphSnapshotV2.model_validate(snapshot.model_dump(mode="python"))
    return GraphSnapshotV2.model_validate(
        {
            "schema_version": 2,
            "nodes": [node.model_dump(mode="python") for node in snapshot.nodes],
            "edges": [edge.model_dump(mode="python") for edge in snapshot.edges],
            "shots": [],
        }
    )


def normalize_to_v3(
    snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
) -> GraphSnapshotV3:
    if isinstance(snapshot, GraphSnapshotV3):
        return GraphSnapshotV3.model_validate(snapshot.model_dump(mode="python"))
    normalized_v2 = normalize_to_v2(snapshot)
    nodes: list[dict[str, object]] = []
    for node in normalized_v2.nodes:
        node_payload = node.model_dump(mode="python")
        if node.kind == "prompt":
            node_payload["data"] = {
                "document": document_from_text(node.data.text).model_dump(mode="python")
            }
        nodes.append(node_payload)
    return GraphSnapshotV3.model_validate(
        {
            "schema_version": 3,
            "nodes": nodes,
            "edges": [edge.model_dump(mode="python") for edge in normalized_v2.edges],
            "shots": [shot.model_dump(mode="python") for shot in normalized_v2.shots],
            "resource_bindings": [],
        }
    )


def canonical_snapshot(
    snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
) -> tuple[str, str]:
    payload = snapshot.model_dump(mode="json")
    payload["nodes"] = sorted(payload["nodes"], key=lambda item: item["id"])
    payload["edges"] = sorted(payload["edges"], key=lambda item: item["id"])
    if isinstance(snapshot, (GraphSnapshotV2, GraphSnapshotV3)):
        payload["shots"] = sorted(payload["shots"], key=lambda item: item["id"])
    if isinstance(snapshot, GraphSnapshotV3):
        payload["resource_bindings"] = sorted(
            payload["resource_bindings"],
            key=lambda item: (
                item["scope_kind"],
                item["scope_id"] or "",
                item["binding_key"],
                item["id"],
            ),
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()
