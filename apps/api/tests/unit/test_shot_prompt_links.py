from uuid import UUID, uuid4

import pytest

from ai_canvas.contracts.graph import (
    GraphEdge,
    GraphSnapshotV3,
    Point,
    PromptNodeV3,
    Shot,
    ShotNodeV3,
)
from ai_canvas.domain.graph.validation import GraphValidationError, validate_graph
from ai_canvas.domain.prompt_batches.shot_prompt_links import (
    PROMPT_DOCUMENT_PORT,
    resolve_shot_prompt_targets,
)
from ai_canvas.domain.prompting.document import document_from_text


def _prompt_node(node_id: UUID | None = None) -> PromptNodeV3:
    return PromptNodeV3(
        id=node_id or uuid4(),
        kind="prompt",
        position=Point(x=0, y=0),
        data={"document": document_from_text("subject")},
    )


def _shot(order: int) -> tuple[Shot, ShotNodeV3]:
    shot = Shot(id=uuid4(), order=order)
    node = ShotNodeV3(
        id=uuid4(),
        kind="shot",
        position=Point(x=100, y=order * 100),
        data={"shot_id": shot.id},
    )
    return shot, node


def _link(prompt: PromptNodeV3, shot_node: ShotNodeV3) -> GraphEdge:
    return GraphEdge(
        id=uuid4(),
        source=prompt.id,
        source_port=PROMPT_DOCUMENT_PORT,
        target=shot_node.id,
        target_port=PROMPT_DOCUMENT_PORT,
    )


def test_valid_one_to_one_links_validate_and_resolve_in_shot_order() -> None:
    later_shot, later_node = _shot(8)
    earlier_shot, earlier_node = _shot(2)
    later_prompt = _prompt_node()
    earlier_prompt = _prompt_node()
    graph = GraphSnapshotV3(
        nodes=[later_node, earlier_prompt, earlier_node, later_prompt],
        edges=[_link(later_prompt, later_node), _link(earlier_prompt, earlier_node)],
        shots=[later_shot, earlier_shot],
    )

    assert validate_graph(graph) == graph
    resolution = resolve_shot_prompt_targets(graph)

    assert resolution.blockers == ()
    assert [target.shot_id for target in resolution.targets] == [
        earlier_shot.id,
        later_shot.id,
    ]
    assert [target.prompt_node_id for target in resolution.targets] == [
        earlier_prompt.id,
        later_prompt.id,
    ]


def test_graph_rejects_multiple_prompt_inputs_for_one_shot() -> None:
    shot, shot_node = _shot(0)
    first = _prompt_node()
    second = _prompt_node()
    graph = GraphSnapshotV3(
        nodes=[first, second, shot_node],
        edges=[_link(first, shot_node), _link(second, shot_node)],
        shots=[shot],
    )

    with pytest.raises(GraphValidationError, match="shot prompt input"):
        validate_graph(graph)


def test_graph_rejects_one_prompt_linked_to_multiple_shots() -> None:
    first_shot, first_node = _shot(0)
    second_shot, second_node = _shot(1)
    prompt = _prompt_node()
    graph = GraphSnapshotV3(
        nodes=[prompt, first_node, second_node],
        edges=[_link(prompt, first_node), _link(prompt, second_node)],
        shots=[first_shot, second_shot],
    )

    with pytest.raises(GraphValidationError, match="prompt node output"):
        validate_graph(graph)


def test_missing_link_is_an_explicit_blocker_not_a_silent_skip() -> None:
    linked_shot, linked_node = _shot(0)
    missing_shot, missing_node = _shot(1)
    prompt = _prompt_node()
    graph = GraphSnapshotV3(
        nodes=[prompt, linked_node, missing_node],
        edges=[_link(prompt, linked_node)],
        shots=[linked_shot, missing_shot],
    )

    assert validate_graph(graph) == graph
    resolution = resolve_shot_prompt_targets(graph)

    assert [target.shot_id for target in resolution.targets] == [linked_shot.id]
    assert [(blocker.shot_id, blocker.code) for blocker in resolution.blockers] == [
        (missing_shot.id, "SHOT_PROMPT_NOT_LINKED")
    ]


def test_selected_shots_report_unknown_ids_and_limit_resolution_scope() -> None:
    first_shot, first_node = _shot(0)
    second_shot, second_node = _shot(1)
    first_prompt = _prompt_node()
    second_prompt = _prompt_node()
    graph = GraphSnapshotV3(
        nodes=[first_prompt, second_prompt, first_node, second_node],
        edges=[_link(first_prompt, first_node), _link(second_prompt, second_node)],
        shots=[first_shot, second_shot],
    )
    unknown_id = uuid4()

    resolution = resolve_shot_prompt_targets(
        graph,
        selected_shot_ids=[second_shot.id, unknown_id],
    )

    assert [target.shot_id for target in resolution.targets] == [second_shot.id]
    assert [(blocker.shot_id, blocker.code) for blocker in resolution.blockers] == [
        (unknown_id, "SHOT_NOT_FOUND")
    ]


def test_existing_v3_graph_without_semantic_links_remains_valid() -> None:
    shot, shot_node = _shot(0)
    graph = GraphSnapshotV3(nodes=[shot_node], shots=[shot])

    assert validate_graph(graph) == graph
