from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from ai_canvas.contracts.prompt_batches import PromptBatchItemDraftV1
from ai_canvas.contracts.prompts import PromptBlock, PromptBlockKind, PromptDocumentV1
from ai_canvas.contracts.resources import PromptResourcePayload
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.prompting.document import (
    render_blocks,
    validate_prompt_document,
)


PROTECTED_KINDS = frozenset({"SUBJECT", "SCENE_ACTION", "MOTION"})
EDITABLE_KINDS = frozenset(
    {
        "COMPOSITION_CAMERA",
        "LIGHT_COLOR_MATERIAL_STYLE",
        "NEGATIVE_CONSTRAINTS",
        "PROVIDER_EXTENSION",
        "FREEFORM",
    }
)


class PromptBatchOutputScopeError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenPromptBlock:
    block_id: UUID
    kind: PromptBlockKind
    locked: bool
    index: int
    text_sha256: str


@dataclass(frozen=True)
class FrozenPromptDocument:
    document_sha256: str
    protected: tuple[FrozenPromptBlock, ...]
    editable: tuple[FrozenPromptBlock, ...]
    protected_manifest_sha256: str
    editable_manifest_sha256: str


@dataclass(frozen=True)
class CompiledPromptBatchItemPayload:
    shot_id: UUID
    prompt_node_id: UUID
    canonical_bytes: bytes
    payload_sha256: str
    provider_payload: dict[str, Any]
    preview: dict[str, Any]
    frozen: FrozenPromptDocument


@dataclass(frozen=True)
class CompiledPromptBatchItemOutput:
    document: PromptDocumentV1
    output_sha256: str
    document_sha256: str


def _manifest_payload(block: FrozenPromptBlock) -> dict[str, object]:
    return {
        "block_id": str(block.block_id),
        "kind": block.kind,
        "locked": block.locked,
        "index": block.index,
        "text_sha256": block.text_sha256,
    }


def freeze_prompt_document(document: PromptDocumentV1) -> FrozenPromptDocument:
    validate_prompt_document(document)
    protected: list[FrozenPromptBlock] = []
    editable: list[FrozenPromptBlock] = []
    for index, block in enumerate(document.blocks):
        frozen = FrozenPromptBlock(
            block_id=block.id,
            kind=block.kind,
            locked=block.locked,
            index=index,
            text_sha256=sha256(block.text.encode("utf-8")).hexdigest(),
        )
        if block.locked or block.kind in PROTECTED_KINDS:
            protected.append(frozen)
        elif block.kind in EDITABLE_KINDS:
            editable.append(frozen)
        else:
            raise ValueError(f"unsupported prompt block kind: {block.kind}")
    protected_payload = [_manifest_payload(item) for item in protected]
    editable_payload = [_manifest_payload(item) for item in editable]
    return FrozenPromptDocument(
        document_sha256=canonical_sha256(document.model_dump(mode="json")),
        protected=tuple(protected),
        editable=tuple(editable),
        protected_manifest_sha256=canonical_sha256(protected_payload),
        editable_manifest_sha256=canonical_sha256(editable_payload),
    )


def _output_schema(*, shot_id: UUID, prompt_node_id: UUID, document_sha256: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "schema_version",
            "shot_id",
            "prompt_node_id",
            "base_document_sha256",
            "replacements",
            "summary",
        ],
        "properties": {
            "kind": {"const": "prompt_batch_item_draft"},
            "schema_version": {"const": 1},
            "shot_id": {"const": str(shot_id)},
            "prompt_node_id": {"const": str(prompt_node_id)},
            "base_document_sha256": {"const": document_sha256},
            "replacements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5000,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block_id", "replacement_text"],
                    "properties": {
                        "block_id": {"type": "string", "format": "uuid"},
                        "replacement_text": {"type": "string", "maxLength": 100000},
                    },
                },
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }


def compile_prompt_batch_item_payload(
    *,
    shot_id: UUID,
    prompt_node_id: UUID,
    document: PromptDocumentV1,
    profile: PromptResourcePayload,
    instruction: str,
    model_id: str,
) -> CompiledPromptBatchItemPayload:
    frozen = freeze_prompt_document(document)
    if not frozen.editable:
        raise ValueError("NO_EDITABLE_BLOCKS")
    editable_ids = {item.block_id for item in frozen.editable}
    contexts = [
        {
            "category": "USER_INSTRUCTION",
            "content": instruction,
            "characters": len(instruction),
        },
        {
            "category": "PROMPT_TARGET_DOCUMENT",
            "content": document.model_dump(mode="json"),
            "characters": len(document.rendered_text),
        },
        {
            "category": "PROMPT_PROFILE_RULES",
            "content": {
                "content_order": profile.content_order,
                "detail_density": profile.detail_density,
                "format_rules": profile.format_rules,
                "camera_rules": profile.camera_rules,
                "positive_rules": profile.positive_rules,
                "negative_rules": profile.negative_rules,
                "applicability": profile.applicability,
            },
        },
        {
            "category": "PRESERVE_CONSTRAINTS",
            "content": {
                "protected": [
                    _manifest_payload(item) for item in frozen.protected
                ],
                "editable_block_ids": [
                    str(block.id)
                    for block in document.blocks
                    if block.id in editable_ids
                ],
            },
        },
    ]
    provider_payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return one JSON object matching the strict schema. Modify every "
                    "editable block and no protected block. Do not request tools."
                ),
            },
            {
                "role": "user",
                "content": {
                    "shot_id": str(shot_id),
                    "prompt_node_id": str(prompt_node_id),
                    "base_document_sha256": frozen.document_sha256,
                    "contexts": contexts,
                },
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "prompt_batch_item_draft_v1",
                "strict": True,
                "schema": _output_schema(
                    shot_id=shot_id,
                    prompt_node_id=prompt_node_id,
                    document_sha256=frozen.document_sha256,
                ),
            },
        },
        "stream": False,
    }
    encoded = canonical_json(provider_payload).encode("utf-8")
    return CompiledPromptBatchItemPayload(
        shot_id=shot_id,
        prompt_node_id=prompt_node_id,
        canonical_bytes=encoded,
        payload_sha256=sha256(encoded).hexdigest(),
        provider_payload=provider_payload,
        preview={
            "model_id": model_id,
            "contexts": contexts,
            "protected_blocks": len(frozen.protected),
            "editable_blocks": len(frozen.editable),
            "protected_manifest": [
                _manifest_payload(item) for item in frozen.protected
            ],
            "editable_manifest": [
                _manifest_payload(item) for item in frozen.editable
            ],
            "truncated": False,
        },
        frozen=frozen,
    )


def compile_batch_item_output(
    *,
    document: PromptDocumentV1,
    profile: PromptResourcePayload,
    expected_shot_id: UUID,
    expected_prompt_node_id: UUID,
    draft: PromptBatchItemDraftV1,
) -> CompiledPromptBatchItemOutput:
    frozen = freeze_prompt_document(document)
    if (
        draft.shot_id != expected_shot_id
        or draft.prompt_node_id != expected_prompt_node_id
        or draft.base_document_sha256 != frozen.document_sha256
    ):
        raise PromptBatchOutputScopeError("output identity or base hash changed")
    expected_ids = {item.block_id for item in frozen.editable}
    replacement_ids = {item.block_id for item in draft.replacements}
    if replacement_ids != expected_ids or len(draft.replacements) != len(expected_ids):
        raise PromptBatchOutputScopeError(
            "output replacements must cover exactly the editable manifest"
        )
    replacements = {
        replacement.block_id: replacement.replacement_text
        for replacement in draft.replacements
    }
    try:
        for value in replacements.values():
            value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PromptBatchOutputScopeError("replacement text is not valid Unicode") from error

    replaced: list[PromptBlock] = []
    for block in document.blocks:
        text = replacements.get(block.id, block.text)
        replaced.append(block.model_copy(update={"text": text}))

    order = {kind: index for index, kind in enumerate(profile.content_order)}
    editable_blocks = [
        block for block in replaced if block.id in expected_ids
    ]
    editable_blocks.sort(
        key=lambda block: (
            order.get(block.kind, len(order)),
            next(item.index for item in frozen.editable if item.block_id == block.id),
        )
    )
    compiled_blocks = list(replaced)
    for index, block in zip(
        (item.index for item in frozen.editable), editable_blocks, strict=True
    ):
        compiled_blocks[index] = block

    rendered_text, spans = render_blocks(compiled_blocks)
    compiled = PromptDocumentV1(
        blocks=compiled_blocks,
        rendered_text=rendered_text,
        spans=spans,
    )
    validate_prompt_document(compiled)
    for protected in frozen.protected:
        current = compiled.blocks[protected.index]
        if (
            current.id != protected.block_id
            or current.kind != protected.kind
            or current.locked != protected.locked
            or sha256(current.text.encode("utf-8")).hexdigest()
            != protected.text_sha256
        ):
            raise PromptBatchOutputScopeError("protected prompt content changed")
    return CompiledPromptBatchItemOutput(
        document=compiled,
        output_sha256=canonical_sha256(draft.model_dump(mode="json")),
        document_sha256=canonical_sha256(compiled.model_dump(mode="json")),
    )
