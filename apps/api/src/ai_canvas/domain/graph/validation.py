from collections import defaultdict, deque
from collections.abc import Mapping
from uuid import UUID

from ai_canvas.contracts.graph import (
    GraphSnapshotV1,
    GraphSnapshotV2,
    GraphSnapshotV3,
    PromptNodeV3,
    ShotNodeV2,
)
from ai_canvas.domain.graph.node_registry import (
    NODE_DEFINITIONS,
    PROMPT_DOCUMENT_PORT,
    NodeDefinition,
)
from ai_canvas.domain.prompting.document import validate_prompt_document


class GraphValidationError(ValueError):
    pass


def validate_graph(
    graph: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
) -> GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3:
    validated = _validate_graph(graph, NODE_DEFINITIONS)
    if isinstance(validated, (GraphSnapshotV2, GraphSnapshotV3)):
        _validate_shots(validated)
    if isinstance(validated, GraphSnapshotV3):
        _validate_v3_prompt_documents_and_bindings(validated)
    return validated


def _validate_shots(graph: GraphSnapshotV2 | GraphSnapshotV3) -> None:
    shot_ids = [shot.id for shot in graph.shots]
    if len(shot_ids) != len(set(shot_ids)):
        raise GraphValidationError("duplicate shot id")

    shot_orders = [shot.order for shot in graph.shots]
    if len(shot_orders) != len(set(shot_orders)):
        raise GraphValidationError("duplicate shot order")

    total_references = 0
    for shot in graph.shots:
        references = shot.reference_revision_ids
        if len(references) > 64:
            raise GraphValidationError("a shot may contain at most 64 reference revisions")
        if len(references) != len(set(references)):
            raise GraphValidationError("duplicate reference revision id")
        total_references += len(references)
    if total_references > 10_000:
        raise GraphValidationError(
            "a graph may contain at most 10,000 shot reference revisions"
        )

    shot_node_ids = [
        node.data.shot_id for node in graph.nodes if isinstance(node, ShotNodeV2)
    ]
    if (
        len(shot_node_ids) != len(shot_ids)
        or len(shot_node_ids) != len(set(shot_node_ids))
        or set(shot_node_ids) != set(shot_ids)
    ):
        raise GraphValidationError("every top-level shot must have exactly one shot node")


def _validate_v3_prompt_documents_and_bindings(graph: GraphSnapshotV3) -> None:
    for node in graph.nodes:
        if isinstance(node, PromptNodeV3):
            try:
                validate_prompt_document(node.data.document)
            except ValueError as error:
                raise GraphValidationError(str(error)) from error

    binding_ids = [binding.id for binding in graph.resource_bindings]
    if len(binding_ids) != len(set(binding_ids)):
        raise GraphValidationError("duplicate resource binding id")
    binding_keys = [
        (binding.scope_kind, binding.scope_id, binding.binding_key)
        for binding in graph.resource_bindings
    ]
    if len(binding_keys) != len(set(binding_keys)):
        raise GraphValidationError("duplicate resource binding key in scope")

    node_ids = {node.id for node in graph.nodes}
    shot_ids = {shot.id for shot in graph.shots}
    for binding in graph.resource_bindings:
        if binding.scope_kind == "SHOT" and binding.scope_id not in shot_ids:
            raise GraphValidationError("resource binding scope target is missing")
        if binding.scope_kind == "NODE" and binding.scope_id not in node_ids:
            raise GraphValidationError("resource binding scope target is missing")


def _validate_graph(
    graph: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
    definitions: Mapping[str, NodeDefinition],
) -> GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3:
    node_ids = [node.id for node in graph.nodes]
    edge_ids = [edge.id for edge in graph.edges]
    if len(node_ids) != len(set(node_ids)):
        raise GraphValidationError("duplicate node id")
    if len(edge_ids) != len(set(edge_ids)):
        raise GraphValidationError("duplicate edge id")

    nodes = {node.id: node for node in graph.nodes}
    adjacency: dict[UUID, set[UUID]] = defaultdict(set)
    indegree = {node_id: 0 for node_id in nodes}

    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise GraphValidationError("edge endpoint is missing")
        if edge.source == edge.target:
            raise GraphValidationError("self-loop is not allowed")
        source_def = definitions[nodes[edge.source].kind]
        target_def = definitions[nodes[edge.target].kind]
        source_type = source_def.outputs.get(edge.source_port)
        target_type = target_def.inputs.get(edge.target_port)
        if source_type is None or target_type is None or source_type != target_type:
            raise GraphValidationError("edge ports are unknown or incompatible")
        if edge.target not in adjacency[edge.source]:
            adjacency[edge.source].add(edge.target)
            indegree[edge.target] += 1

    prompt_output_counts: dict[UUID, int] = defaultdict(int)
    shot_input_counts: dict[UUID, int] = defaultdict(int)
    for edge in graph.edges:
        if edge.source_port != PROMPT_DOCUMENT_PORT:
            continue
        prompt_output_counts[edge.source] += 1
        shot_input_counts[edge.target] += 1
    if any(count > 1 for count in prompt_output_counts.values()):
        raise GraphValidationError(
            "a prompt node output may link to at most one shot prompt input"
        )
    if any(count > 1 for count in shot_input_counts.values()):
        raise GraphValidationError(
            "a shot prompt input may link from at most one prompt node output"
        )

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise GraphValidationError("graph contains a cycle")
    return graph
