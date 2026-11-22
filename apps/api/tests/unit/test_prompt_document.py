from uuid import UUID, uuid4

from pydantic import ValidationError
import pytest

from ai_canvas.contracts.prompts import PromptBlock, PromptDocumentV1, PromptSpan
from ai_canvas.domain.prompting.document import (
    document_from_text,
    render_blocks,
    utf16_length,
    validate_prompt_document,
)


def _block(text: str, *, block_id: UUID | None = None, locked: bool = False) -> PromptBlock:
    return PromptBlock(
        id=block_id or uuid4(),
        kind="FREEFORM",
        text=text,
        locked=locked,
    )


def test_render_uses_utf16_offsets_for_emoji_and_cjk() -> None:
    document = document_from_text("人🙂物")

    assert document.rendered_text == "人🙂物"
    assert document.spans[0].start_utf16 == 0
    assert document.spans[0].end_utf16 == 4
    assert utf16_length(document.rendered_text) == 4


def test_render_tracks_each_block_around_single_newline_separators() -> None:
    first = _block("🙂")
    second = _block("人物")

    rendered_text, spans = render_blocks([first, second])

    assert rendered_text == "🙂\n人物"
    assert [(span.block_id, span.start_utf16, span.end_utf16) for span in spans] == [
        (first.id, 0, 2),
        (second.id, 3, 5),
    ]


def test_submitted_render_cannot_disagree_with_blocks() -> None:
    prompt_block = _block("原文")
    with pytest.raises(ValueError, match="rendered_text and spans"):
        validate_prompt_document(
            PromptDocumentV1(
                blocks=[prompt_block],
                rendered_text="改文",
                spans=[
                    PromptSpan(
                        block_id=prompt_block.id,
                        start_utf16=0,
                        end_utf16=2,
                    )
                ],
            )
        )


def test_prompt_document_rejects_duplicate_block_ids() -> None:
    duplicate_id = uuid4()
    blocks = [_block("one", block_id=duplicate_id), _block("two", block_id=duplicate_id)]
    rendered_text, spans = render_blocks(blocks)

    with pytest.raises(ValueError, match="block ids must be unique"):
        validate_prompt_document(
            PromptDocumentV1(blocks=blocks, rendered_text=rendered_text, spans=spans)
        )


def test_document_from_text_preserves_exact_legacy_text() -> None:
    source = "  第一行\r\n第二行🙂  "

    document = document_from_text(source)

    assert document.rendered_text == source
    assert document.blocks[0].text == source
    assert document.blocks[0].kind == "FREEFORM"
    assert document.blocks[0].locked is False
    assert validate_prompt_document(document) is document


def test_prompt_models_forbid_extra_fields_and_overlong_blocks() -> None:
    with pytest.raises(ValidationError):
        PromptBlock.model_validate(
            {
                "id": str(uuid4()),
                "kind": "FREEFORM",
                "text": "ok",
                "locked": False,
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        _block("x" * 100_001)
