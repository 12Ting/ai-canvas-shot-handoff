from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from ai_canvas.contracts.prompts import PromptDocumentV1
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.prompt_profiles.rule_program import (
    APPLICABILITY_RULES,
    CAMERA_RULES,
    FORMAT_RULES,
    NEGATIVE_RULES,
    POSITIVE_RULES,
)
from ai_canvas.domain.prompting.document import validate_prompt_document


class PromptProfileExampleTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPromptProfileExtractionPayload:
    source_node_id: UUID
    source_document_sha256: str
    canonical_bytes: bytes
    payload_sha256: str
    provider_payload: dict[str, Any]
    preview: dict[str, Any]


def _output_schema() -> dict[str, Any]:
    block_kinds = [
        "SUBJECT",
        "SCENE_ACTION",
        "COMPOSITION_CAMERA",
        "LIGHT_COLOR_MATERIAL_STYLE",
        "NEGATIVE_CONSTRAINTS",
        "MOTION",
        "PROVIDER_EXTENSION",
        "FREEFORM",
    ]

    def codes(values) -> dict[str, Any]:
        return {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(values)},
        }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "schema_version",
            "content_order",
            "detail_density",
            "format_rule_codes",
            "camera_rule_codes",
            "positive_rule_codes",
            "negative_rule_codes",
            "applicability_codes",
            "summary",
        ],
        "properties": {
            "kind": {"const": "prompt_profile_draft"},
            "schema_version": {"const": 1},
            "content_order": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "items": {"type": "string", "enum": block_kinds},
            },
            "detail_density": {"enum": ["LOW", "MEDIUM", "HIGH"]},
            "format_rule_codes": {
                **codes(FORMAT_RULES),
                "minItems": 1,
                "maxItems": len(FORMAT_RULES),
            },
            "camera_rule_codes": {
                **codes(CAMERA_RULES),
                "maxItems": len(CAMERA_RULES),
            },
            "positive_rule_codes": {
                **codes(POSITIVE_RULES),
                "maxItems": len(POSITIVE_RULES),
            },
            "negative_rule_codes": {
                **codes(NEGATIVE_RULES),
                "maxItems": len(NEGATIVE_RULES),
            },
            "applicability_codes": {
                **codes(APPLICABILITY_RULES),
                "minItems": 1,
                "maxItems": len(APPLICABILITY_RULES),
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }


def compile_prompt_profile_extraction_payload(
    *,
    source_node_id: UUID,
    document: PromptDocumentV1,
    instruction: str,
    model_id: str,
    max_example_characters: int = 20_000,
) -> CompiledPromptProfileExtractionPayload:
    validate_prompt_document(document)
    character_count = len(document.rendered_text)
    if character_count > max_example_characters:
        raise PromptProfileExampleTooLarge(
            "prompt profile example exceeds the declared limit"
        )
    source_hash = canonical_sha256(document.model_dump(mode="json"))
    contexts = [
        {
            "category": "USER_INSTRUCTION",
            "content": instruction,
            "characters": len(instruction),
        },
        {
            "category": "PROMPT_EXAMPLE_DOCUMENT",
            "content": document.model_dump(mode="json"),
            "characters": character_count,
        },
    ]
    provider_payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract reusable form only. Return one strict JSON object using "
                    "only the supplied rule codes. Never copy names, story facts, dialogue, "
                    "brands, or example excerpts. Do not request tools."
                ),
            },
            {
                "role": "user",
                "content": {
                    "source_node_id": str(source_node_id),
                    "source_document_sha256": source_hash,
                    "contexts": contexts,
                },
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "prompt_profile_draft_v1",
                "strict": True,
                "schema": _output_schema(),
            },
        },
        "stream": False,
    }
    encoded = canonical_json(provider_payload).encode("utf-8")
    return CompiledPromptProfileExtractionPayload(
        source_node_id=source_node_id,
        source_document_sha256=source_hash,
        canonical_bytes=encoded,
        payload_sha256=sha256(encoded).hexdigest(),
        provider_payload=provider_payload,
        preview={
            "model_id": model_id,
            "contexts": contexts,
            "original_characters": character_count,
            "sent_characters": character_count,
            "truncated": False,
            "source_document_sha256": source_hash,
        },
    )
