from uuid import uuid4

from ai_canvas.contracts.prompts import PromptBlock, PromptDocumentV1, PromptSpan


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def render_blocks(blocks: list[PromptBlock]) -> tuple[str, list[PromptSpan]]:
    rendered_parts: list[str] = []
    spans: list[PromptSpan] = []
    cursor = 0
    for block in blocks:
        if rendered_parts:
            rendered_parts.append("\n")
            cursor += 1
        rendered_parts.append(block.text)
        end = cursor + utf16_length(block.text)
        spans.append(
            PromptSpan(
                block_id=block.id,
                start_utf16=cursor,
                end_utf16=end,
            )
        )
        cursor = end
    return "".join(rendered_parts), spans


def validate_prompt_document(document: PromptDocumentV1) -> PromptDocumentV1:
    block_ids = [block.id for block in document.blocks]
    if len(set(block_ids)) != len(block_ids):
        raise ValueError("prompt block ids must be unique")
    expected_text, expected_spans = render_blocks(document.blocks)
    if document.rendered_text != expected_text or document.spans != expected_spans:
        raise ValueError("submitted rendered_text and spans do not match prompt blocks")
    return document


def document_from_text(value: str) -> PromptDocumentV1:
    block = PromptBlock(
        id=uuid4(),
        kind="FREEFORM",
        text=value,
        locked=False,
    )
    rendered_text, spans = render_blocks([block])
    return PromptDocumentV1(
        blocks=[block],
        rendered_text=rendered_text,
        spans=spans,
    )
