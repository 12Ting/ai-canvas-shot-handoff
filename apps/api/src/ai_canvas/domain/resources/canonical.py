import hashlib
import json
import re

from ai_canvas.contracts.resources import (
    CharacterResourcePayload,
    MediaResourcePayload,
    NormalizedResourceTag,
    PromptResourcePayload,
    ResourceArtifactReference,
    ResourcePayload,
    StyleResourcePayload,
)


_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\)[^\s,;]+"
)
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.])/(?:[^/\s]+/)*[^\s,;]+")


def canonical_resource_payload(payload: ResourcePayload) -> tuple[str, str]:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_resource_tags(values: list[str]) -> list[NormalizedResourceTag]:
    normalized: list[NormalizedResourceTag] = []
    seen: set[str] = set()
    for raw_value in values:
        display_tag = raw_value.strip()
        normalized_tag = display_tag.casefold()
        if not display_tag:
            raise ValueError("resource tags cannot be empty")
        if len(display_tag) > 100 or len(normalized_tag) > 100:
            raise ValueError("resource tags may contain at most 100 characters")
        if normalized_tag in seen:
            raise ValueError(f"duplicate resource tag: {display_tag}")
        seen.add(normalized_tag)
        normalized.append(
            NormalizedResourceTag(
                display_tag=display_tag,
                normalized_tag=normalized_tag,
            )
        )
    return normalized


def canonical_artifact_references(
    values: list[ResourceArtifactReference],
) -> list[ResourceArtifactReference]:
    seen: set[tuple[str, int]] = set()
    for value in values:
        key = (value.slot, value.position)
        if key in seen:
            raise ValueError(
                f"duplicate artifact slot position: {value.slot}[{value.position}]"
            )
        seen.add(key)
    return sorted(
        values,
        key=lambda value: (value.slot, value.position, str(value.artifact_revision_id)),
    )


def resource_search_text(
    name: str,
    tags: list[NormalizedResourceTag],
    payload: ResourcePayload,
) -> str:
    fragments = [name]
    for tag in tags:
        fragments.extend((tag.display_tag, tag.normalized_tag))
    fragments.extend(_searchable_payload_fragments(payload))
    safe_fragments: list[str] = []
    for fragment in fragments:
        without_windows_paths = _WINDOWS_ABSOLUTE_PATH.sub(" ", fragment)
        without_absolute_paths = _POSIX_ABSOLUTE_PATH.sub(" ", without_windows_paths)
        normalized_whitespace = " ".join(without_absolute_paths.split())
        if normalized_whitespace:
            safe_fragments.append(normalized_whitespace.casefold())
    return " ".join(safe_fragments)


def _searchable_payload_fragments(payload: ResourcePayload) -> list[str]:
    if isinstance(payload, StyleResourcePayload):
        return [
            payload.summary,
            *payload.palette,
            *payload.lighting,
            *payload.materials,
            *payload.camera_language,
            *payload.negative_constraints,
        ]
    if isinstance(payload, CharacterResourcePayload):
        return [
            payload.canonical_name,
            payload.identity_anchor,
            *payload.locked_traits,
            *payload.wardrobe_notes,
        ]
    if isinstance(payload, PromptResourcePayload):
        return [
            *payload.content_order,
            payload.detail_density,
            *payload.format_rules,
            *payload.camera_rules,
            *payload.positive_rules,
            *payload.negative_rules,
            *payload.applicability,
        ]
    if isinstance(payload, MediaResourcePayload):
        fragments = [payload.usage_notes]
        if payload.expected_media_kind is not None:
            fragments.append(payload.expected_media_kind)
        return fragments
    raise TypeError(f"unsupported resource payload type: {type(payload).__name__}")
