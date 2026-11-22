from collections.abc import MutableMapping
from types import MappingProxyType
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_canvas.contracts import graph as graph_contracts
from ai_canvas.contracts.graph import GraphEdge, GraphNode, GraphSnapshotV1, Point, ViewportState
from ai_canvas.domain.graph import validation as graph_validation
from ai_canvas.domain.graph.node_registry import NODE_DEFINITIONS, NodeDefinition
from ai_canvas.domain.graph.validation import GraphValidationError, validate_graph


def node(kind: str) -> GraphNode:
    return GraphNode(id=uuid4(), kind=kind, position=Point(x=0, y=0), data={"text": "hello"})


def test_valid_text_to_prompt_edge() -> None:
    source = node("text_source")
    target = node("prompt")
    graph = GraphSnapshotV1(
        nodes=[source, target],
        edges=[GraphEdge(id=uuid4(), source=source.id, source_port="text", target=target.id, target_port="text")],
    )

    assert validate_graph(graph) == graph


def test_accepts_distinct_parallel_edges() -> None:
    source = node("text_source")
    target = node("prompt")
    edges = [
        GraphEdge(
            id=uuid4(),
            source=source.id,
            source_port="text",
            target=target.id,
            target_port="text",
        )
        for _ in range(2)
    ]
    graph = GraphSnapshotV1(nodes=[source, target], edges=edges)

    assert validate_graph(graph) == graph


@pytest.mark.parametrize("field_name", ["source_port", "target_port"])
@pytest.mark.parametrize("port_name", ["", "x" * 65, "-leading-hyphen", "invalid/port"])
def test_rejects_invalid_port_names(field_name: str, port_name: str) -> None:
    edge_data: dict[str, object] = {
        "id": uuid4(),
        "source": uuid4(),
        "source_port": "text",
        "target": uuid4(),
        "target_port": "prompt",
    }
    edge_data[field_name] = port_name

    with pytest.raises(ValidationError):
        GraphEdge(**edge_data)


@pytest.mark.parametrize(
    ("source_port", "target_port"),
    [("text", "prompt"), ("_source-1.value", "target_2")],
)
def test_accepts_semantic_port_names(source_port: str, target_port: str) -> None:
    edge = GraphEdge(
        id=uuid4(),
        source=uuid4(),
        source_port=source_port,
        target=uuid4(),
        target_port=target_port,
    )

    assert edge.source_port == source_port
    assert edge.target_port == target_port


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("x", float("inf")), ("x", float("-inf")), ("y", float("nan"))],
)
def test_point_rejects_non_finite_coordinates(field_name: str, value: float) -> None:
    coordinates = {"x": 0.0, "y": 0.0}
    coordinates[field_name] = value

    with pytest.raises(ValidationError):
        Point(**coordinates)


@pytest.mark.parametrize("zoom", [0.1, 4.0])
def test_viewport_accepts_zoom_boundaries(zoom: float) -> None:
    assert ViewportState(zoom=zoom).zoom == zoom


@pytest.mark.parametrize("zoom", [0.09, 4.01, float("nan"), float("inf")])
def test_viewport_rejects_zoom_outside_boundaries(zoom: float) -> None:
    with pytest.raises(ValidationError):
        ViewportState(zoom=zoom)


@pytest.mark.parametrize("kind", ["text_source", "prompt", "note"])
def test_accepts_literal_node_kinds(kind: str) -> None:
    assert node(kind).kind == kind


@pytest.mark.parametrize("kind", ["", "future"])
def test_rejects_unknown_node_kinds(kind: str) -> None:
    with pytest.raises(ValidationError):
        node(kind)


def test_schema_version_is_literal_one() -> None:
    assert GraphSnapshotV1().schema_version == 1
    with pytest.raises(ValidationError):
        GraphSnapshotV1(schema_version=2)


def test_graph_node_list_limit() -> None:
    valid_node = node("note")

    assert len(GraphSnapshotV1(nodes=[valid_node] * 5_000).nodes) == 5_000
    with pytest.raises(ValidationError):
        GraphSnapshotV1(nodes=[valid_node] * 5_001)


def test_graph_edge_list_limit() -> None:
    valid_edge = GraphEdge(
        id=uuid4(),
        source=uuid4(),
        source_port="text",
        target=uuid4(),
        target_port="prompt",
    )

    assert len(GraphSnapshotV1(edges=[valid_edge] * 10_000).edges) == 10_000
    with pytest.raises(ValidationError):
        GraphSnapshotV1(edges=[valid_edge] * 10_001)


@pytest.mark.parametrize(
    ("source_kind", "source_port", "target_kind", "target_port"),
    [
        ("note", "text", "prompt", "text"),
        ("text_source", "missing", "prompt", "text"),
        ("text_source", "text", "note", "text"),
    ],
)
def test_rejects_unknown_or_incompatible_ports(
    source_kind: str, source_port: str, target_kind: str, target_port: str
) -> None:
    source = node(source_kind)
    target = node(target_kind)
    graph = GraphSnapshotV1(
        nodes=[source, target],
        edges=[
            GraphEdge(
                id=uuid4(),
                source=source.id,
                source_port=source_port,
                target=target.id,
                target_port=target_port,
            )
        ],
    )

    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_rejects_cycle() -> None:
    definitions = MappingProxyType(
        {
            "prompt": NodeDefinition(
                inputs=MappingProxyType({"text": "text/plain"}),
                outputs=MappingProxyType({"prompt": "text/plain"}),
            )
        }
    )
    first = node("prompt")
    second = node("prompt")
    graph = GraphSnapshotV1(
        nodes=[first, second],
        edges=[
            GraphEdge(id=uuid4(), source=first.id, source_port="prompt", target=second.id, target_port="text"),
            GraphEdge(id=uuid4(), source=second.id, source_port="prompt", target=first.id, target_port="text"),
        ],
    )

    private_validate = getattr(graph_validation, "_validate_graph", None)
    assert callable(private_validate)
    with pytest.raises(GraphValidationError, match="cycle"):
        private_validate(graph, definitions)


def test_node_registry_is_immutable() -> None:
    registry = cast(MutableMapping[str, NodeDefinition], NODE_DEFINITIONS)
    try:
        with pytest.raises(TypeError):
            registry["future"] = NodeDefinition(inputs={}, outputs={})
    finally:
        if isinstance(registry, dict):
            registry.pop("future", None)


@pytest.mark.parametrize(
    ("kind", "direction"),
    [("prompt", "inputs"), ("text_source", "outputs")],
)
def test_node_registry_port_maps_are_immutable(kind: str, direction: str) -> None:
    ports = cast(MutableMapping[str, str], getattr(NODE_DEFINITIONS[kind], direction))
    original_ports = dict(ports)
    try:
        with pytest.raises(TypeError):
            ports["future"] = "text/plain"
    finally:
        if isinstance(ports, dict):
            ports.clear()
            ports.update(original_ports)


def test_rejects_duplicate_node_and_edge_ids() -> None:
    source = node("text_source")
    target = node("prompt")
    duplicate_node = GraphNode(
        id=source.id,
        kind="note",
        position=Point(x=20, y=20),
        data={"text": "duplicate"},
    )
    with pytest.raises(GraphValidationError, match="duplicate node"):
        validate_graph(GraphSnapshotV1(nodes=[source, duplicate_node]))

    edge_id = uuid4()
    edge = GraphEdge(
        id=edge_id,
        source=source.id,
        source_port="text",
        target=target.id,
        target_port="text",
    )
    with pytest.raises(GraphValidationError, match="duplicate edge"):
        validate_graph(GraphSnapshotV1(nodes=[source, target], edges=[edge, edge.model_copy()]))


def test_rejects_missing_endpoint_and_self_loop() -> None:
    source = node("text_source")
    target = node("prompt")
    missing = GraphEdge(
        id=uuid4(),
        source=source.id,
        source_port="text",
        target=uuid4(),
        target_port="text",
    )
    with pytest.raises(GraphValidationError, match="endpoint"):
        validate_graph(GraphSnapshotV1(nodes=[source, target], edges=[missing]))

    self_loop = GraphEdge(
        id=uuid4(),
        source=source.id,
        source_port="text",
        target=source.id,
        target_port="text",
    )
    with pytest.raises(GraphValidationError, match="self-loop"):
        validate_graph(GraphSnapshotV1(nodes=[source], edges=[self_loop]))


def test_node_text_contract_rejects_hidden_fields_and_unbounded_text() -> None:
    with pytest.raises(ValidationError, match="command"):
        GraphNode(
            id=uuid4(),
            kind="note",
            position=Point(x=0, y=0),
            data={"text": "ok", "command": "hidden"},
        )
    with pytest.raises(ValidationError, match="100000"):
        GraphNode(
            id=uuid4(),
            kind="note",
            position=Point(x=0, y=0),
            data={"text": "x" * 100_001},
        )


def _shot_payload(
    *,
    shot_id=None,
    node_id=None,
    order: int = 0,
    references=None,
) -> tuple[dict[str, object], object, object]:
    shot_id = shot_id or uuid4()
    node_id = node_id or uuid4()
    payload: dict[str, object] = {
        "schema_version": 2,
        "nodes": [
            {
                "id": node_id,
                "kind": "shot",
                "position": {"x": 0, "y": 0},
                "data": {"shot_id": shot_id},
            }
        ],
        "edges": [],
        "shots": [
            {
                "id": shot_id,
                "order": order,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": references or [],
            }
        ],
    }
    return payload, shot_id, node_id


@pytest.mark.parametrize(
    ("artifact_revision_id", "media_kind"),
    [(uuid4(), None), (None, "image")],
)
def test_v2_media_revision_and_kind_are_both_null_or_both_present(
    artifact_revision_id,
    media_kind,
) -> None:
    with pytest.raises(ValidationError, match="artifact_revision_id.*media_kind"):
        graph_contracts.GraphSnapshotV2.model_validate(
            {
                "schema_version": 2,
                "nodes": [
                    {
                        "id": uuid4(),
                        "kind": "media_input",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "artifact_revision_id": artifact_revision_id,
                            "media_kind": media_kind,
                        },
                    }
                ],
                "edges": [],
                "shots": [],
            }
        )

    empty = graph_contracts.GraphSnapshotV2.model_validate(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": uuid4(),
                    "kind": "media_input",
                    "position": {"x": 0, "y": 0},
                    "data": {"artifact_revision_id": None, "media_kind": None},
                }
            ],
            "edges": [],
            "shots": [],
        }
    )
    assert empty.nodes[0].data.artifact_revision_id is None

    revision_id = uuid4()
    attached = graph_contracts.GraphSnapshotV2.model_validate(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": uuid4(),
                    "kind": "media_input",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "artifact_revision_id": revision_id,
                        "media_kind": "image",
                    },
                }
            ],
            "edges": [],
            "shots": [],
        }
    )
    assert attached.nodes[0].data.artifact_revision_id == revision_id


def test_v2_node_union_rejects_kind_data_mismatch() -> None:
    with pytest.raises(ValidationError):
        graph_contracts.GraphSnapshotV2.model_validate(
            {
                "schema_version": 2,
                "nodes": [
                    {
                        "id": uuid4(),
                        "kind": "media_input",
                        "position": {"x": 0, "y": 0},
                        "data": {"text": "not media data"},
                    }
                ],
                "edges": [],
                "shots": [],
            }
        )


def test_v2_shots_require_unique_ids_orders_and_references() -> None:
    first_id = uuid4()
    second_id = uuid4()
    first_node = uuid4()
    second_node = uuid4()
    base = {
        "schema_version": 2,
        "nodes": [
            {
                "id": first_node,
                "kind": "shot",
                "position": {"x": 0, "y": 0},
                "data": {"shot_id": first_id},
            },
            {
                "id": second_node,
                "kind": "shot",
                "position": {"x": 1, "y": 1},
                "data": {"shot_id": second_id},
            },
        ],
        "edges": [],
        "shots": [
            {
                "id": first_id,
                "order": 0,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": [],
            },
            {
                "id": second_id,
                "order": 1,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": [],
            },
        ],
    }
    assert validate_graph(graph_contracts.GraphSnapshotV2.model_validate(base)).schema_version == 2

    duplicate_id = graph_contracts.GraphSnapshotV2.model_validate(
        {**base, "shots": [base["shots"][0], base["shots"][0]]}
    )
    with pytest.raises(GraphValidationError, match="duplicate shot id"):
        validate_graph(duplicate_id)

    duplicate_order_shots = [dict(base["shots"][0]), dict(base["shots"][1])]
    duplicate_order_shots[1]["order"] = 0
    duplicate_order = graph_contracts.GraphSnapshotV2.model_validate(
        {**base, "shots": duplicate_order_shots}
    )
    with pytest.raises(GraphValidationError, match="duplicate shot order"):
        validate_graph(duplicate_order)

    reference_id = uuid4()
    duplicate_reference_shots = [dict(base["shots"][0]), dict(base["shots"][1])]
    duplicate_reference_shots[0]["reference_revision_ids"] = [reference_id, reference_id]
    duplicate_reference = graph_contracts.GraphSnapshotV2.model_validate(
        {**base, "shots": duplicate_reference_shots}
    )
    with pytest.raises(GraphValidationError, match="duplicate reference"):
        validate_graph(duplicate_reference)

    negative_order_shots = [dict(base["shots"][0]), dict(base["shots"][1])]
    negative_order_shots[0]["order"] = -1
    with pytest.raises(ValidationError):
        graph_contracts.GraphSnapshotV2.model_validate(
            {**base, "shots": negative_order_shots}
        )


@pytest.mark.parametrize("mode", ["missing_node", "orphan_node", "duplicate_node"])
def test_v2_requires_exactly_one_shot_node_per_top_level_shot(mode: str) -> None:
    payload, shot_id, _node_id = _shot_payload()
    nodes = list(payload["nodes"])
    shots = list(payload["shots"])
    if mode == "missing_node":
        nodes = []
    elif mode == "orphan_node":
        orphan_id = uuid4()
        nodes.append(
            {
                "id": uuid4(),
                "kind": "shot",
                "position": {"x": 1, "y": 1},
                "data": {"shot_id": orphan_id},
            }
        )
    else:
        nodes.append(
            {
                "id": uuid4(),
                "kind": "shot",
                "position": {"x": 2, "y": 2},
                "data": {"shot_id": shot_id},
            }
        )
    payload["nodes"] = nodes
    payload["shots"] = shots

    graph = graph_contracts.GraphSnapshotV2.model_validate(payload)

    with pytest.raises(GraphValidationError, match="exactly one shot node"):
        validate_graph(graph)


def test_shot_order_accepts_javascript_safe_integer_boundary() -> None:
    maximum = 9_007_199_254_740_991
    payload, _shot_id, _node_id = _shot_payload(order=maximum)

    assert validate_graph(
        graph_contracts.GraphSnapshotV2.model_validate(payload)
    ).shots[0].order == maximum

    payload["shots"][0]["order"] = maximum + 1
    with pytest.raises(ValidationError):
        graph_contracts.GraphSnapshotV2.model_validate(payload)


def _raw_snapshot_with_reference_counts(counts: list[int]) -> dict[str, object]:
    nodes = []
    shots = []
    for order, count in enumerate(counts):
        shot_id = uuid4()
        nodes.append(
            {
                "id": uuid4(),
                "kind": "shot",
                "position": {"x": order, "y": 0},
                "data": {"shot_id": shot_id},
            }
        )
        shots.append(
            {
                "id": shot_id,
                "order": order,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": [uuid4() for _ in range(count)],
            }
        )
    return {"schema_version": 2, "nodes": nodes, "edges": [], "shots": shots}


def _snapshot_with_reference_counts(counts: list[int]):
    return graph_contracts.GraphSnapshotV2.model_validate(
        _raw_snapshot_with_reference_counts(counts)
    )


def _unchecked_snapshot_with_reference_counts(counts: list[int]):
    raw = _raw_snapshot_with_reference_counts(counts)
    nodes = [graph_contracts.ShotNodeV2.model_validate(node) for node in raw["nodes"]]
    shots = [graph_contracts.Shot.model_construct(**shot) for shot in raw["shots"]]
    return graph_contracts.GraphSnapshotV2.model_construct(
        schema_version=2,
        nodes=nodes,
        edges=[],
        shots=shots,
    )


def test_shot_reference_per_shot_limit_has_exact_boundary() -> None:
    assert validate_graph(_snapshot_with_reference_counts([64])).schema_version == 2

    raw = _raw_snapshot_with_reference_counts([65])
    raw["shots"][0]["reference_revision_ids"] = ["not-a-uuid"] * 65
    with pytest.raises(ValidationError, match="at most 64"):
        graph_contracts.GraphSnapshotV2.model_validate(raw)

    with pytest.raises(GraphValidationError, match="at most 64"):
        validate_graph(_unchecked_snapshot_with_reference_counts([65]))


def test_shot_reference_total_budget_has_exact_boundary() -> None:
    assert validate_graph(
        _snapshot_with_reference_counts(([64] * 156) + [16])
    ).schema_version == 2

    raw = _raw_snapshot_with_reference_counts(([64] * 156) + [17])
    raw["shots"][0]["reference_revision_ids"][0] = "not-a-uuid"
    with pytest.raises(ValidationError, match="10,000"):
        graph_contracts.GraphSnapshotV2.model_validate(raw)

    with pytest.raises(GraphValidationError, match="10,000"):
        validate_graph(
            _unchecked_snapshot_with_reference_counts(([64] * 156) + [17])
        )


@pytest.mark.parametrize("kind", ["media_input"])
def test_media_nodes_have_no_ports(kind: str) -> None:
    prompt_id = uuid4()
    if kind == "shot":
        payload, _shot_id, source_id = _shot_payload()
    else:
        source_id = uuid4()
        payload = {
            "schema_version": 2,
            "nodes": [
                {
                    "id": source_id,
                    "kind": "media_input",
                    "position": {"x": 0, "y": 0},
                    "data": {"artifact_revision_id": None, "media_kind": None},
                }
            ],
            "edges": [],
            "shots": [],
        }
    payload["nodes"].append(
        {
            "id": prompt_id,
            "kind": "prompt",
            "position": {"x": 3, "y": 3},
            "data": {"text": ""},
        }
    )
    payload["edges"] = [
        {
            "id": uuid4(),
            "source": source_id,
            "source_port": "text",
            "target": prompt_id,
            "target_port": "text",
        }
    ]
    graph = graph_contracts.GraphSnapshotV2.model_validate(payload)

    with pytest.raises(GraphValidationError, match="ports"):
        validate_graph(graph)
