from uuid import uuid4

import pytest

from ai_canvas.contracts.prompt_batches import (
    PromptBatchItemDraftV1,
    PromptBlockReplacement,
)
from ai_canvas.contracts.prompts import PromptBlock, PromptDocumentV1
from ai_canvas.contracts.resources import PromptResourcePayload
from ai_canvas.domain.prompt_batches.payload import (
    PromptBatchOutputScopeError,
    compile_batch_item_output,
    compile_prompt_batch_item_payload,
    freeze_prompt_document,
)
from ai_canvas.domain.prompting.document import render_blocks


def _document() -> PromptDocumentV1:
    blocks = [
        PromptBlock(id=uuid4(), kind="SUBJECT", text="Mira", locked=False),
        PromptBlock(
            id=uuid4(),
            kind="LIGHT_COLOR_MATERIAL_STYLE",
            text="soft daylight",
            locked=False,
        ),
        PromptBlock(id=uuid4(), kind="SCENE_ACTION", text="opens door", locked=False),
        PromptBlock(
            id=uuid4(),
            kind="COMPOSITION_CAMERA",
            text="wide shot",
            locked=False,
        ),
        PromptBlock(id=uuid4(), kind="FREEFORM", text="locked fact", locked=True),
    ]
    rendered_text, spans = render_blocks(blocks)
    return PromptDocumentV1(blocks=blocks, rendered_text=rendered_text, spans=spans)


def _profile() -> PromptResourcePayload:
    return PromptResourcePayload(
        content_order=[
            "COMPOSITION_CAMERA",
            "LIGHT_COLOR_MATERIAL_STYLE",
            "SUBJECT",
            "SCENE_ACTION",
            "FREEFORM",
        ],
        detail_density="HIGH",
        format_rules=["每个结构块单独成段"],
        camera_rules=["明确写出景别"],
        positive_rules=["包含光线描述"],
        negative_rules=[],
        applicability=["适用于通用视觉提示词"],
        source_example_sha256="a" * 64,
    )


def test_freeze_marks_fact_and_locked_blocks_protected() -> None:
    document = _document()
    frozen = freeze_prompt_document(document)

    assert [item.kind for item in frozen.protected] == [
        "SUBJECT",
        "SCENE_ACTION",
        "FREEFORM",
    ]
    assert [item.kind for item in frozen.editable] == [
        "LIGHT_COLOR_MATERIAL_STYLE",
        "COMPOSITION_CAMERA",
    ]
    assert frozen.document_sha256 == freeze_prompt_document(document).document_sha256


def test_provider_payload_is_deterministic_and_contains_no_profile_example() -> None:
    document = _document()
    profile = _profile()
    compiled = compile_prompt_batch_item_payload(
        shot_id=uuid4(),
        prompt_node_id=uuid4(),
        document=document,
        profile=profile,
        instruction="Improve editable visual language",
        model_id="prompt-editor-v1",
    )

    assert compiled.payload_sha256 == compile_prompt_batch_item_payload(
        shot_id=compiled.shot_id,
        prompt_node_id=compiled.prompt_node_id,
        document=document,
        profile=profile,
        instruction="Improve editable visual language",
        model_id="prompt-editor-v1",
    ).payload_sha256
    encoded = compiled.canonical_bytes.decode("utf-8")
    assert "Mira" in encoded
    assert profile.source_example_sha256 not in encoded
    assert "example" not in encoded.lower()
    assert compiled.preview["truncated"] is False
    assert compiled.preview["model_id"] == "prompt-editor-v1"
    assert compiled.preview["contexts"][0] == {
        "category": "USER_INSTRUCTION",
        "content": "Improve editable visual language",
        "characters": len("Improve editable visual language"),
    }
    assert compiled.preview["contexts"][1]["content"] == document.model_dump(mode="json")
    assert compiled.preview["contexts"][2]["content"]["content_order"] == profile.content_order
    assert compiled.preview["contexts"][3]["content"]["protected"]
    assert compiled.preview["protected_manifest"] == [
        {
            "block_id": str(item.block_id),
            "kind": item.kind,
            "locked": item.locked,
            "index": item.index,
            "text_sha256": item.text_sha256,
        }
        for item in compiled.frozen.protected
    ]
    assert compiled.preview["editable_manifest"] == [
        {
            "block_id": str(item.block_id),
            "kind": item.kind,
            "locked": item.locked,
            "index": item.index,
            "text_sha256": item.text_sha256,
        }
        for item in compiled.frozen.editable
    ]


def test_compile_output_requires_exact_editable_set_and_preserves_anchor_indexes() -> None:
    document = _document()
    frozen = freeze_prompt_document(document)
    replacements = [
        PromptBlockReplacement(block_id=item.block_id, replacement_text=f"new-{item.kind}")
        for item in reversed(frozen.editable)
    ]
    draft = PromptBatchItemDraftV1(
        shot_id=uuid4(),
        prompt_node_id=uuid4(),
        base_document_sha256=frozen.document_sha256,
        replacements=replacements,
        summary="updated",
    )

    compiled = compile_batch_item_output(
        document=document,
        profile=_profile(),
        expected_shot_id=draft.shot_id,
        expected_prompt_node_id=draft.prompt_node_id,
        draft=draft,
    )

    assert [compiled.document.blocks[index].id for index in (0, 2, 4)] == [
        document.blocks[index].id for index in (0, 2, 4)
    ]
    assert [compiled.document.blocks[index].text for index in (0, 2, 4)] == [
        document.blocks[index].text for index in (0, 2, 4)
    ]
    assert [compiled.document.blocks[index].kind for index in (1, 3)] == [
        "COMPOSITION_CAMERA",
        "LIGHT_COLOR_MATERIAL_STYLE",
    ]

    incomplete = draft.model_copy(update={"replacements": replacements[:1]})
    with pytest.raises(PromptBatchOutputScopeError, match="exactly"):
        compile_batch_item_output(
            document=document,
            profile=_profile(),
            expected_shot_id=draft.shot_id,
            expected_prompt_node_id=draft.prompt_node_id,
            draft=incomplete,
        )


def test_compile_output_rejects_identity_or_base_hash_drift() -> None:
    document = _document()
    frozen = freeze_prompt_document(document)
    draft = PromptBatchItemDraftV1(
        shot_id=uuid4(),
        prompt_node_id=uuid4(),
        base_document_sha256="f" * 64,
        replacements=[
            PromptBlockReplacement(block_id=item.block_id, replacement_text="new")
            for item in frozen.editable
        ],
        summary="updated",
    )

    with pytest.raises(PromptBatchOutputScopeError, match="identity or base"):
        compile_batch_item_output(
            document=document,
            profile=_profile(),
            expected_shot_id=draft.shot_id,
            expected_prompt_node_id=draft.prompt_node_id,
            draft=draft,
        )
