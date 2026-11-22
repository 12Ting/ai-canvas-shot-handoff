from hashlib import sha256

import pytest
from pydantic import ValidationError

from ai_canvas.contracts.prompt_profile_extractions import PromptProfileDraftV1
from ai_canvas.domain.prompt_profiles.rule_program import (
    compile_profile_draft,
    rule_code_set_sha256,
)


def draft_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "prompt_profile_draft",
        "schema_version": 1,
        "content_order": ["SUBJECT", "SCENE_ACTION", "COMPOSITION_CAMERA"],
        "detail_density": "MEDIUM",
        "format_rule_codes": ["SECTION_PER_BLOCK", "PRESERVE_LANGUAGE"],
        "camera_rule_codes": ["EXPLICIT_SHOT_SIZE"],
        "positive_rule_codes": ["INCLUDE_LIGHTING"],
        "negative_rule_codes": ["NO_TEXT_WATERMARK"],
        "applicability_codes": ["IMAGE_PROMPT"],
        "summary": "提炼结构与镜头规则",
    }
    payload.update(overrides)
    return payload


def test_profile_draft_accepts_only_finite_rule_codes() -> None:
    draft = PromptProfileDraftV1.model_validate(draft_payload())

    assert draft.format_rule_codes == ["SECTION_PER_BLOCK", "PRESERVE_LANGUAGE"]
    with pytest.raises(ValidationError):
        PromptProfileDraftV1.model_validate(
            draft_payload(format_rule_codes=["复制角色 Gwen 的服装"])
        )
    with pytest.raises(ValidationError):
        PromptProfileDraftV1.model_validate(
            draft_payload(format_rule_codes=["SECTION_PER_BLOCK", "SECTION_PER_BLOCK"])
        )
    with pytest.raises(ValidationError):
        PromptProfileDraftV1.model_validate(draft_payload(example_excerpt="Gwen"))


def test_rule_program_compiles_to_existing_prompt_resource_without_example_text() -> None:
    draft = PromptProfileDraftV1.model_validate(draft_payload())
    source_hash = sha256("Gwen 在 Moon Mall".encode("utf-8")).hexdigest()

    payload = compile_profile_draft(draft, source_example_sha256=source_hash)
    serialized = payload.model_dump_json()

    assert payload.kind == "PROMPT"
    assert payload.source_example_sha256 == source_hash
    assert payload.format_rules == [
        "每个结构块单独成段",
        "保持目标提示词的原始语言",
    ]
    assert "Gwen" not in serialized
    assert "Moon Mall" not in serialized
    assert rule_code_set_sha256() == rule_code_set_sha256()
    assert len(rule_code_set_sha256()) == 64


def test_rule_codes_must_stay_in_their_declared_family() -> None:
    with pytest.raises(ValidationError):
        PromptProfileDraftV1.model_validate(
            draft_payload(format_rule_codes=["EXPLICIT_LENS"])
        )
