from uuid import UUID

from pydantic import TypeAdapter

from ai_canvas.contracts.agent_proposals import (
    AgentGraphChangeSetDraftV1,
    AgentScope,
)
from ai_canvas.contracts.graph import GraphSnapshotV3
from ai_canvas.domain.prompting.document import render_blocks, utf16_length


_scope_adapter = TypeAdapter(AgentScope)


class AgentScopeError(ValueError):
    pass


def _python_index(value: str, utf16_offset: int) -> int:
    if utf16_offset < 0 or utf16_offset > utf16_length(value):
        raise AgentScopeError("UTF-16 selection is outside node text")
    cursor = 0
    for index, character in enumerate(value):
        if cursor == utf16_offset:
            return index
        cursor += utf16_length(character)
        if cursor > utf16_offset:
            raise AgentScopeError("UTF-16 selection splits a surrogate pair")
    if cursor == utf16_offset:
        return len(value)
    raise AgentScopeError("UTF-16 selection is outside node text")


def _replace_utf16(value: str, start: int, end: int, replacement: str) -> str:
    start_index = _python_index(value, start)
    end_index = _python_index(value, end)
    if end_index < start_index:
        raise AgentScopeError("UTF-16 selection order is invalid")
    return value[:start_index] + replacement + value[end_index:]


def slice_utf16(value: str, start: int, end: int) -> str:
    start_index = _python_index(value, start)
    end_index = _python_index(value, end)
    if end_index < start_index:
        raise AgentScopeError("UTF-16 selection order is invalid")
    return value[start_index:end_index]


def compile_graph_draft(
    graph: GraphSnapshotV3,
    *,
    node_id: UUID,
    scope: AgentScope | dict,
    draft: AgentGraphChangeSetDraftV1,
) -> GraphSnapshotV3:
    validated_scope = _scope_adapter.validate_python(scope)
    if draft.operation.node_id != node_id:
        raise AgentScopeError("model output targets a different node")
    node_index = next(
        (index for index, node in enumerate(graph.nodes) if node.id == node_id),
        None,
    )
    if node_index is None:
        raise AgentScopeError("Agent target node does not exist")
    node = graph.nodes[node_index]
    replacement = draft.operation.replacement_text

    if node.kind in {"text_source", "note"}:
        old_text = node.data.text
        if validated_scope.kind == "NODE":
            new_text = replacement
        else:
            new_text = _replace_utf16(
                old_text,
                validated_scope.start_utf16,
                validated_scope.end_utf16,
                replacement,
            )
        new_node = node.model_copy(
            update={"data": node.data.model_copy(update={"text": new_text})}
        )
    elif node.kind == "prompt":
        document = node.data.document
        blocks = [block.model_copy(deep=True) for block in document.blocks]
        if validated_scope.kind == "NODE":
            editable = [index for index, block in enumerate(blocks) if not block.locked]
            if len(editable) != 1:
                raise AgentScopeError(
                    "prompt NODE scope requires exactly one unlocked prompt block"
                )
            index = editable[0]
            blocks[index] = blocks[index].model_copy(update={"text": replacement})
        else:
            span_index = next(
                (
                    index
                    for index, span in enumerate(document.spans)
                    if validated_scope.start_utf16 >= span.start_utf16
                    and validated_scope.end_utf16 <= span.end_utf16
                ),
                None,
            )
            if span_index is None:
                raise AgentScopeError("selection must remain inside one prompt block")
            block = blocks[span_index]
            if block.locked:
                raise AgentScopeError("selection targets a locked prompt block")
            span = document.spans[span_index]
            updated_text = _replace_utf16(
                block.text,
                validated_scope.start_utf16 - span.start_utf16,
                validated_scope.end_utf16 - span.start_utf16,
                replacement,
            )
            blocks[span_index] = block.model_copy(update={"text": updated_text})
        rendered_text, spans = render_blocks(blocks)
        updated_document = document.model_copy(
            update={"blocks": blocks, "rendered_text": rendered_text, "spans": spans}
        )
        new_node = node.model_copy(
            update={"data": node.data.model_copy(update={"document": updated_document})}
        )
    else:
        raise AgentScopeError("Agent text scope does not support this node kind")

    nodes = list(graph.nodes)
    nodes[node_index] = new_node
    return graph.model_copy(update={"nodes": nodes}, deep=True)
