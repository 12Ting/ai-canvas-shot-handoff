from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from ai_canvas.contracts.resources import (
    CharacterResourcePayload,
    MediaResourcePayload,
    PromptResourcePayload,
    ResourceArtifactReference,
    ResourcePayload,
    StyleResourcePayload,
)
from ai_canvas.domain.resources.canonical import (
    canonical_artifact_references,
    canonical_resource_payload,
    normalize_resource_tags,
    resource_search_text,
)


RESOURCE_PAYLOAD_ADAPTER = TypeAdapter(ResourcePayload)


@pytest.mark.parametrize(
    ("raw", "expected_type"),
    [
        ({"kind": "STYLE", "summary": "低饱和"}, StyleResourcePayload),
        (
            {
                "kind": "CHARACTER",
                "canonical_name": "Gwen",
                "identity_anchor": "short dark hair",
            },
            CharacterResourcePayload,
        ),
        (
            {
                "kind": "PROMPT",
                "content_order": ["SUBJECT", "SCENE_ACTION"],
                "detail_density": "MEDIUM",
                "format_rules": ["subject first"],
            },
            PromptResourcePayload,
        ),
        ({"kind": "MEDIA", "usage_notes": "hero reference"}, MediaResourcePayload),
    ],
)
def test_resource_payload_is_strictly_discriminated(raw: dict[str, object], expected_type: type) -> None:
    parsed = RESOURCE_PAYLOAD_ADAPTER.validate_python(raw)

    assert isinstance(parsed, expected_type)

    with pytest.raises(ValidationError):
        RESOURCE_PAYLOAD_ADAPTER.validate_python({**raw, "unexpected": "field"})


def test_resource_payload_rejects_empty_required_text_and_excessive_lists() -> None:
    with pytest.raises(ValidationError):
        StyleResourcePayload(summary="")
    with pytest.raises(ValidationError):
        PromptResourcePayload(
            content_order=["SUBJECT"],
            detail_density="MEDIUM",
            format_rules=["rule"] * 65,
        )


def test_prompt_resource_hash_is_key_order_independent() -> None:
    left = PromptResourcePayload(
        content_order=["SUBJECT", "SCENE_ACTION"],
        detail_density="MEDIUM",
        format_rules=["subject first"],
    )
    right = PromptResourcePayload.model_validate(
        dict(reversed(list(left.model_dump().items())))
    )

    left_json, left_hash = canonical_resource_payload(left)
    right_json, right_hash = canonical_resource_payload(right)

    assert left_json == right_json
    assert left_hash == right_hash
    assert '": ' not in left_json
    assert '", ' not in left_json
    assert "\\u" not in left_json


def test_tags_use_unicode_casefold_and_reject_normalized_duplicates() -> None:
    tags = normalize_resource_tags(["  Straße  ", "电影感"])

    assert [(tag.display_tag, tag.normalized_tag) for tag in tags] == [
        ("Straße", "strasse"),
        ("电影感", "电影感"),
    ]
    with pytest.raises(ValueError, match="duplicate resource tag"):
        normalize_resource_tags(["Straße", "STRASSE"])


def test_artifact_references_are_sorted_and_duplicate_slots_rejected() -> None:
    first_id = uuid4()
    second_id = uuid4()
    ordered = canonical_artifact_references(
        [
            ResourceArtifactReference(
                slot="reference",
                position=1,
                artifact_revision_id=second_id,
            ),
            ResourceArtifactReference(
                slot="hero",
                position=0,
                artifact_revision_id=first_id,
            ),
        ]
    )

    assert [(item.slot, item.position) for item in ordered] == [
        ("hero", 0),
        ("reference", 1),
    ]
    with pytest.raises(ValueError, match="duplicate artifact slot position"):
        canonical_artifact_references(
            [
                ResourceArtifactReference(
                    slot="hero",
                    position=0,
                    artifact_revision_id=first_id,
                ),
                ResourceArtifactReference(
                    slot="hero",
                    position=0,
                    artifact_revision_id=second_id,
                ),
            ]
        )


def test_search_text_uses_allowlisted_fields_without_absolute_paths_or_hidden_hashes() -> None:
    hidden_hash = "f" * 64
    payload = PromptResourcePayload(
        content_order=["SUBJECT"],
        detail_density="HIGH",
        format_rules=["Use D:\\private\\reference.png", "portrait"],
        applicability=["/srv/private/example.png", "人物"],
        source_example_sha256=hidden_hash,
    )
    tags = normalize_resource_tags(["  Cinematic "])

    search_text = resource_search_text("主角模板", tags, payload)

    assert "主角模板" in search_text
    assert "cinematic" in search_text
    assert "portrait" in search_text
    assert "人物" in search_text
    assert "D:\\private" not in search_text
    assert "/srv/private" not in search_text
    assert hidden_hash not in search_text
