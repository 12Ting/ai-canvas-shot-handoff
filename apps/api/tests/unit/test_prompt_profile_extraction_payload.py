from uuid import uuid4

import pytest

from ai_canvas.contracts.prompts import PromptBlock, PromptDocumentV1
from ai_canvas.domain.prompt_profiles.extraction_payload import (
    PromptProfileExampleTooLarge,
    compile_prompt_profile_extraction_payload,
)
from ai_canvas.domain.prompting.document import render_blocks


def _document(text: str) -> PromptDocumentV1:
    block = PromptBlock(id=uuid4(), kind="FREEFORM", text=text, locked=False)
    rendered_text, spans = render_blocks([block])
    return PromptDocumentV1(blocks=[block], rendered_text=rendered_text, spans=spans)


def test_extraction_payload_freezes_complete_document_without_truncation() -> None:
    document = _document("portrait, amber rim light")
    compiled = compile_prompt_profile_extraction_payload(
        source_node_id=uuid4(),
        document=document,
        instruction="Extract reusable form only",
        model_id="prompt-editor-v1",
        max_example_characters=100,
    )

    assert compiled.preview["original_characters"] == len(document.rendered_text)
    assert compiled.preview["sent_characters"] == len(document.rendered_text)
    assert compiled.preview["truncated"] is False
    assert compiled.preview["model_id"] == "prompt-editor-v1"
    assert compiled.preview["contexts"] == [
        {
            "category": "USER_INSTRUCTION",
            "content": "Extract reusable form only",
            "characters": len("Extract reusable form only"),
        },
        {
            "category": "PROMPT_EXAMPLE_DOCUMENT",
            "content": document.model_dump(mode="json"),
            "characters": len(document.rendered_text),
        },
    ]

    assert document.rendered_text in compiled.canonical_bytes.decode("utf-8")
    assert compiled.payload_sha256 == compile_prompt_profile_extraction_payload(
        source_node_id=compiled.source_node_id,
        document=document,
        instruction="Extract reusable form only",
        model_id="prompt-editor-v1",
        max_example_characters=100,
    ).payload_sha256


def test_extraction_payload_rejects_oversize_example_instead_of_truncating() -> None:
    with pytest.raises(PromptProfileExampleTooLarge):
        compile_prompt_profile_extraction_payload(
            source_node_id=uuid4(),
            document=_document("123456"),
            instruction="Extract",
            model_id="prompt-editor-v1",
            max_example_characters=5,
        )
