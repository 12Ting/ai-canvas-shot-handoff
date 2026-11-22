from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Literal
from uuid import UUID

from ai_canvas.contracts.graph import GraphSnapshotV1, GraphSnapshotV2, GraphSnapshotV3


SparseState = Literal["CURRENT", "STALE"]
VisibleState = Literal["UNMATERIALIZED", "CURRENT", "STALE", "ABSENT"]
ImpactKind = Literal["DIRECT", "DOWNSTREAM", "REMOVED"]

REASON_ORDER = (
    "NODE_ADDED",
    "NODE_REMOVED",
    "NODE_TYPE_CHANGED",
    "NODE_DATA_CHANGED",
    "PROMPT_DOCUMENT_CHANGED",
    "INCOMING_EDGES_CHANGED",
    "RESOURCE_BINDING_CHANGED",
    "DOWNSTREAM_OF_CHANGE",
)
_REASON_INDEX = {reason: index for index, reason in enumerate(REASON_ORDER)}


@dataclass(frozen=True)
class SparseMaterializationState:
    state: SparseState
    last_materialized_graph_revision_id: UUID
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImpactItem:
    node_id: UUID
    impact_kind: ImpactKind
    reasons: tuple[str, ...]
    old_state: VisibleState
    projected_state: VisibleState


@dataclass(frozen=True)
class ProjectedMaterializationRow:
    node_id: UUID
    state: SparseState
    last_materialized_graph_revision_id: UUID
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ComputedImpact:
    items: tuple[ImpactItem, ...]
    projected_rows: tuple[ProjectedMaterializationRow, ...]
    impact_sha256: str
    confirmation_required: bool


GraphSnapshot = GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _node_data(node: object) -> str:
    return _canonical(node.data.model_dump(mode="json"))


def _incoming(snapshot: GraphSnapshot) -> dict[UUID, tuple[tuple[str, str, str], ...]]:
    values: dict[UUID, list[tuple[str, str, str]]] = defaultdict(list)
    for edge in snapshot.edges:
        values[edge.target].append(
            (str(edge.source), str(edge.source_port), str(edge.target_port))
        )
    return {node_id: tuple(sorted(edges)) for node_id, edges in values.items()}


def _shot_for_node(node: object) -> UUID | None:
    if getattr(node, "kind", None) != "shot":
        return None
    return node.data.shot_id


def _resolved_bindings(snapshot: GraphSnapshot, node: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(snapshot, GraphSnapshotV3):
        return ()
    shot_id = _shot_for_node(node)
    effective: dict[tuple[str, str], tuple[int, str]] = {}
    for binding in snapshot.resource_bindings:
        precedence = -1
        if binding.scope_kind == "PROJECT":
            precedence = 0
        elif binding.scope_kind == "SHOT" and shot_id is not None and binding.scope_id == shot_id:
            precedence = 1
        elif binding.scope_kind == "NODE" and binding.scope_id == node.id:
            precedence = 2
        if precedence < 0:
            continue
        key = (binding.resource_kind, str(binding.binding_key))
        candidate = (precedence, str(binding.resource_revision_id))
        current = effective.get(key)
        if current is None or candidate > current:
            effective[key] = candidate
    return tuple(
        sorted((kind, key, revision_id) for (kind, key), (_, revision_id) in effective.items())
    )


def _direct_changes(
    base: GraphSnapshot, proposed: GraphSnapshot
) -> dict[UUID, set[str]]:
    base_nodes = {node.id: node for node in base.nodes}
    proposed_nodes = {node.id: node for node in proposed.nodes}
    base_incoming = _incoming(base)
    proposed_incoming = _incoming(proposed)
    direct: dict[UUID, set[str]] = defaultdict(set)
    for node_id in base_nodes.keys() | proposed_nodes.keys():
        before = base_nodes.get(node_id)
        after = proposed_nodes.get(node_id)
        if before is None:
            direct[node_id].add("NODE_ADDED")
            continue
        if after is None:
            direct[node_id].add("NODE_REMOVED")
            continue
        if before.kind != after.kind:
            direct[node_id].add("NODE_TYPE_CHANGED")
        if _node_data(before) != _node_data(after):
            direct[node_id].add("NODE_DATA_CHANGED")
            if before.kind == "prompt" or after.kind == "prompt":
                direct[node_id].add("PROMPT_DOCUMENT_CHANGED")
        if base_incoming.get(node_id, ()) != proposed_incoming.get(node_id, ()):
            direct[node_id].add("INCOMING_EDGES_CHANGED")
        if _resolved_bindings(base, before) != _resolved_bindings(proposed, after):
            direct[node_id].add("RESOURCE_BINDING_CHANGED")
    return direct


def _downstream(snapshot: GraphSnapshot, roots: set[UUID]) -> set[UUID]:
    outgoing: dict[UUID, set[UUID]] = defaultdict(set)
    for edge in snapshot.edges:
        outgoing[edge.source].add(edge.target)
    visited = set(roots)
    result: set[UUID] = set()
    queue = deque(sorted(roots, key=str))
    while queue:
        source = queue.popleft()
        for target in sorted(outgoing.get(source, ()), key=str):
            if target in visited:
                continue
            visited.add(target)
            result.add(target)
            queue.append(target)
    return result


def _sorted_reasons(reasons: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(reasons), key=lambda item: (_REASON_INDEX.get(item, 999), item)))


def compute_stale_impact(
    *,
    base: GraphSnapshot,
    proposed: GraphSnapshot,
    base_states: Mapping[UUID, SparseMaterializationState],
) -> ComputedImpact:
    direct = _direct_changes(base, proposed)
    proposed_ids = {node.id for node in proposed.nodes}
    base_ids = {node.id for node in base.nodes}
    downstream = _downstream(proposed, set(direct)) - set(direct)
    items: list[ImpactItem] = []
    projected_rows: list[ProjectedMaterializationRow] = []
    confirmation_required = False

    affected = set(direct) | downstream
    for node_id in sorted(affected, key=str):
        state = base_states.get(node_id)
        old_state: VisibleState
        if node_id not in base_ids:
            old_state = "ABSENT"
        elif state is None:
            old_state = "UNMATERIALIZED"
        else:
            old_state = state.state
        removed = node_id not in proposed_ids
        if removed:
            projected_state: VisibleState = "ABSENT"
            impact_kind: ImpactKind = "REMOVED"
            reasons = direct[node_id]
        else:
            impact_kind = "DIRECT" if node_id in direct else "DOWNSTREAM"
            reasons = direct[node_id] if node_id in direct else {"DOWNSTREAM_OF_CHANGE"}
            if state is None:
                projected_state = "UNMATERIALIZED"
            else:
                projected_state = "STALE"
                combined_reasons = _sorted_reasons(set(state.reasons) | set(reasons))
                projected_rows.append(
                    ProjectedMaterializationRow(
                        node_id=node_id,
                        state="STALE",
                        last_materialized_graph_revision_id=(
                            state.last_materialized_graph_revision_id
                        ),
                        reasons=combined_reasons,
                    )
                )
                if state.state == "CURRENT":
                    confirmation_required = True
        items.append(
            ImpactItem(
                node_id=node_id,
                impact_kind=impact_kind,
                reasons=_sorted_reasons(reasons),
                old_state=old_state,
                projected_state=projected_state,
            )
        )

    for node_id, state in sorted(base_states.items(), key=lambda item: str(item[0])):
        if node_id in affected or node_id not in proposed_ids:
            continue
        projected_rows.append(
            ProjectedMaterializationRow(
                node_id=node_id,
                state=state.state,
                last_materialized_graph_revision_id=state.last_materialized_graph_revision_id,
                reasons=_sorted_reasons(state.reasons),
            )
        )
    projected_rows.sort(key=lambda row: str(row.node_id))
    payload = [
        {
            "node_id": str(item.node_id),
            "impact_kind": item.impact_kind,
            "reasons": list(item.reasons),
            "old_state": item.old_state,
            "projected_state": item.projected_state,
        }
        for item in items
    ]
    impact_sha256 = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return ComputedImpact(
        items=tuple(items),
        projected_rows=tuple(projected_rows),
        impact_sha256=impact_sha256,
        confirmation_required=confirmation_required,
    )
