import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_canvas.application.graph_service import canonical_snapshot
from ai_canvas.contracts import graph as graph_contracts
from ai_canvas.contracts.graph import GraphSnapshotV1
from ai_canvas.domain.graph.validation import validate_graph


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "graph_snapshot_v1.json"
EXPECTED_CANONICAL_SHA256 = "d6d67b276ed51f289005bb259ed2e3f906e5ef9dc2a78bcfeb77816935656381"


def test_graph_snapshot_v1_fixture_round_trips_and_canonicalizes_stably() -> None:
    snapshot = GraphSnapshotV1.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert validate_graph(snapshot) == snapshot
    assert GraphSnapshotV1.model_validate_json(snapshot.model_dump_json()) == snapshot

    nodes_by_kind = {node.kind: node for node in snapshot.nodes}
    assert len(snapshot.nodes) == 3
    assert set(nodes_by_kind) == {"text_source", "prompt", "note"}
    source = nodes_by_kind["text_source"]
    prompt = nodes_by_kind["prompt"]
    note = nodes_by_kind["note"]
    assert source.data.text == "黄昏的纸船驶向星海 🚀"
    assert prompt.data.text == "电影感构图，暖金色逆光，保留水面倒影 ✨"
    assert note.data.text == "备注：中文与 emoji 都必须稳定往返 🧪"

    assert len(snapshot.edges) == 1
    edge = snapshot.edges[0]
    assert (edge.source, edge.source_port, edge.target, edge.target_port) == (
        source.id,
        "text",
        prompt.id,
        "text",
    )

    first_json, first_hash = canonical_snapshot(snapshot)
    second_json, second_hash = canonical_snapshot(snapshot)

    assert first_json == second_json
    assert first_hash == second_hash
    assert first_hash == EXPECTED_CANONICAL_SHA256
    assert GraphSnapshotV1.model_validate_json(first_json) == snapshot


def test_v1_contract_rejects_v2_node_kinds_without_changing_fixture_hash() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["nodes"][0]["kind"] = "media_input"
    payload["nodes"][0]["data"] = {
        "artifact_revision_id": None,
        "media_kind": None,
    }

    with pytest.raises(ValidationError):
        GraphSnapshotV1.model_validate(payload)

    fixture = GraphSnapshotV1.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert canonical_snapshot(fixture)[1] == EXPECTED_CANONICAL_SHA256


def test_snapshot_union_parses_both_versions_and_upgrade_is_pure() -> None:
    from ai_canvas.domain.graph.snapshots import normalize_to_v2, parse_snapshot_json

    graph_snapshot = graph_contracts.GraphSnapshot
    graph_snapshot_v2 = graph_contracts.GraphSnapshotV2
    v1 = GraphSnapshotV1.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))
    before = v1.model_dump(mode="json")

    first = normalize_to_v2(v1)
    second = normalize_to_v2(v1)

    assert isinstance(first, graph_snapshot_v2)
    assert first == second
    assert first is not second
    assert first.schema_version == 2
    assert first.shots == []
    assert [node.kind for node in first.nodes] == [node.kind for node in v1.nodes]
    assert v1.model_dump(mode="json") == before
    assert isinstance(parse_snapshot_json(v1.model_dump_json()), GraphSnapshotV1)
    assert isinstance(parse_snapshot_json(first.model_dump_json()), graph_snapshot_v2)
    assert TypeAdapter(graph_snapshot).validate_python(first.model_dump(mode="json")) == first


def test_v1_union_and_stored_parser_default_an_omitted_schema_version_without_mutation() -> None:
    from ai_canvas.domain.graph.snapshots import parse_snapshot_json

    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    del payload["schema_version"]
    before = json.loads(json.dumps(payload))

    parsed_from_mapping = TypeAdapter(graph_contracts.GraphSnapshot).validate_python(payload)
    parsed_from_storage = parse_snapshot_json(json.dumps(payload))

    assert isinstance(parsed_from_mapping, GraphSnapshotV1)
    assert isinstance(parsed_from_storage, GraphSnapshotV1)
    assert parsed_from_mapping.schema_version == 1
    assert parsed_from_storage.schema_version == 1
    assert payload == before
    assert "schema_version" not in payload
