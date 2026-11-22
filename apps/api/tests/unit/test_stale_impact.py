from uuid import UUID

from ai_canvas.contracts.graph import (
    GraphEdge,
    GraphSnapshotV2,
    NoteNodeV2,
    NodeTextData,
    Point,
)
from ai_canvas.domain.branching.stale_impact import (
    SparseMaterializationState,
    compute_stale_impact,
)


A = UUID("00000000-0000-4000-8000-000000000001")
B = UUID("00000000-0000-4000-8000-000000000002")
EDGE = UUID("00000000-0000-4000-8000-000000000003")
MATERIALIZED_REVISION = UUID("00000000-0000-4000-8000-000000000004")


def _graph(a_text: str, *, include_b: bool = True) -> GraphSnapshotV2:
    nodes = [
        NoteNodeV2(id=A, kind="note", position=Point(x=0, y=0), data=NodeTextData(text=a_text))
    ]
    edges: list[GraphEdge] = []
    if include_b:
        nodes.append(
            NoteNodeV2(
                id=B,
                kind="note",
                position=Point(x=1, y=1),
                data=NodeTextData(text="b"),
            )
        )
        edges.append(
            GraphEdge(
                id=EDGE,
                source=A,
                source_port="out",
                target=B,
                target_port="in",
            )
        )
    return GraphSnapshotV2(nodes=nodes, edges=edges)


def test_current_downstream_becomes_stale_but_unmaterialized_stays_unmaterialized() -> None:
    result = compute_stale_impact(
        base=_graph("old"),
        proposed=_graph("new"),
        base_states={
            A: SparseMaterializationState(
                state="CURRENT",
                last_materialized_graph_revision_id=MATERIALIZED_REVISION,
                reasons=(),
            )
        },
    )

    by_node = {item.node_id: item for item in result.items}
    assert by_node[A].impact_kind == "DIRECT"
    assert by_node[A].projected_state == "STALE"
    assert by_node[B].impact_kind == "DOWNSTREAM"
    assert by_node[B].projected_state == "UNMATERIALIZED"
    assert result.confirmation_required is True
    assert [row.node_id for row in result.projected_rows] == [A]


def test_removed_node_projects_absent_and_is_not_copied() -> None:
    result = compute_stale_impact(
        base=_graph("same"),
        proposed=_graph("same", include_b=False),
        base_states={
            B: SparseMaterializationState(
                state="STALE",
                last_materialized_graph_revision_id=MATERIALIZED_REVISION,
                reasons=("NODE_DATA_CHANGED",),
            )
        },
    )
    removed = next(item for item in result.items if item.node_id == B)
    assert removed.impact_kind == "REMOVED"
    assert removed.projected_state == "ABSENT"
    assert all(row.node_id != B for row in result.projected_rows)


def test_impact_hash_and_order_are_deterministic() -> None:
    hashes = {
        compute_stale_impact(
            base=_graph("old"),
            proposed=_graph("new"),
            base_states={},
        ).impact_sha256
        for _ in range(100)
    }
    assert len(hashes) == 1
