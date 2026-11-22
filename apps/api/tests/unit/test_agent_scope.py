from uuid import uuid4

import pytest

from ai_canvas.contracts.agent_proposals import AgentGraphChangeSetDraftV1
from ai_canvas.contracts.graph import (
    GraphEdge,
    GraphSnapshotV3,
    NoteNodeV3,
    Point,
    PromptNodeV3,
    TextSourceNodeV3,
)
from ai_canvas.contracts.prompts import PromptBlock, PromptDocumentV1
from ai_canvas.domain.agent.scope import AgentScopeError, compile_graph_draft
from ai_canvas.domain.prompting.document import render_blocks


def _prompt_document(*blocks: PromptBlock) -> PromptDocumentV1:
    rendered, spans = render_blocks(list(blocks))
    return PromptDocumentV1(blocks=list(blocks), rendered_text=rendered, spans=spans)


def _draft(node_id, replacement: str) -> AgentGraphChangeSetDraftV1:
    return AgentGraphChangeSetDraftV1(
        operation={
            "op": "replace_text",
            "node_id": node_id,
            "replacement_text": replacement,
        },
        summary="single bounded replacement",
    )


def test_text_selection_uses_utf16_and_preserves_everything_outside_scope() -> None:
    node_id = uuid4()
    other_id = uuid4()
    edge_id = uuid4()
    graph = GraphSnapshotV3(
        nodes=[
            TextSourceNodeV3(
                id=node_id,
                kind="text_source",
                position=Point(x=10, y=20),
                data={"text": "甲🙂乙丙"},
            ),
            NoteNodeV3(
                id=other_id,
                kind="note",
                position=Point(x=30, y=40),
                data={"text": "保持不变"},
            ),
        ],
        edges=[
            GraphEdge(
                id=edge_id,
                source=node_id,
                source_port="out",
                target=other_id,
                target_port="in",
            )
        ],
    )
    before = graph.model_dump(mode="json")

    compiled = compile_graph_draft(
        graph,
        node_id=node_id,
        scope={"kind": "TEXT_SELECTION", "start_utf16": 1, "end_utf16": 3},
        draft=_draft(node_id, "新"),
    )

    assert compiled.nodes[0].data.text == "甲新乙丙"
    assert compiled.nodes[1] == graph.nodes[1]
    assert compiled.edges == graph.edges
    assert compiled.nodes[0].position == graph.nodes[0].position
    assert graph.model_dump(mode="json") == before


def test_selection_rejects_surrogate_split_and_out_of_bounds_without_partial_mutation() -> None:
    node_id = uuid4()
    graph = GraphSnapshotV3(
        nodes=[
            TextSourceNodeV3(
                id=node_id,
                kind="text_source",
                position=Point(x=0, y=0),
                data={"text": "A🙂B"},
            )
        ]
    )
    before = graph.model_dump(mode="json")
    for scope in (
        {"kind": "TEXT_SELECTION", "start_utf16": 2, "end_utf16": 3},
        {"kind": "TEXT_SELECTION", "start_utf16": 0, "end_utf16": 99},
    ):
        with pytest.raises(AgentScopeError):
            compile_graph_draft(
                graph,
                node_id=node_id,
                scope=scope,
                draft=_draft(node_id, "x"),
            )
    assert graph.model_dump(mode="json") == before


def test_prompt_selection_must_stay_inside_one_unlocked_block() -> None:
    node_id = uuid4()
    locked = PromptBlock(id=uuid4(), kind="SUBJECT", text="锁定🙂", locked=True)
    editable = PromptBlock(id=uuid4(), kind="FREEFORM", text="可编辑文字", locked=False)
    document = _prompt_document(locked, editable)
    graph = GraphSnapshotV3(
        nodes=[
            PromptNodeV3(
                id=node_id,
                kind="prompt",
                position=Point(x=0, y=0),
                data={"document": document},
            )
        ]
    )
    editable_span = document.spans[1]
    compiled = compile_graph_draft(
        graph,
        node_id=node_id,
        scope={
            "kind": "TEXT_SELECTION",
            "start_utf16": editable_span.start_utf16 + 1,
            "end_utf16": editable_span.end_utf16,
        },
        draft=_draft(node_id, "更新"),
    )
    prompt = compiled.nodes[0]
    assert prompt.data.document.blocks[0] == locked
    assert prompt.data.document.blocks[1].text == "可更新"

    with pytest.raises(AgentScopeError, match="locked"):
        compile_graph_draft(
            graph,
            node_id=node_id,
            scope={
                "kind": "TEXT_SELECTION",
                "start_utf16": document.spans[0].start_utf16,
                "end_utf16": document.spans[0].end_utf16,
            },
            draft=_draft(node_id, "越权"),
        )
    with pytest.raises(AgentScopeError, match="one prompt block"):
        compile_graph_draft(
            graph,
            node_id=node_id,
            scope={
                "kind": "TEXT_SELECTION",
                "start_utf16": document.spans[0].end_utf16,
                "end_utf16": document.spans[1].start_utf16 + 1,
            },
            draft=_draft(node_id, "越界"),
        )


def test_prompt_node_scope_replaces_only_the_one_unlocked_block() -> None:
    node_id = uuid4()
    locked = PromptBlock(id=uuid4(), kind="SUBJECT", text="角色锁定", locked=True)
    editable = PromptBlock(id=uuid4(), kind="FREEFORM", text="旧动作", locked=False)
    graph = GraphSnapshotV3(
        nodes=[
            PromptNodeV3(
                id=node_id,
                kind="prompt",
                position=Point(x=0, y=0),
                data={"document": _prompt_document(locked, editable)},
            )
        ]
    )
    compiled = compile_graph_draft(
        graph,
        node_id=node_id,
        scope={"kind": "NODE"},
        draft=_draft(node_id, "新动作"),
    )
    assert [block.text for block in compiled.nodes[0].data.document.blocks] == [
        "角色锁定",
        "新动作",
    ]
