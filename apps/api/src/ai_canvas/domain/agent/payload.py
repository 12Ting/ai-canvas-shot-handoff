from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from ai_canvas.contracts.agent_proposals import AgentScope
from ai_canvas.contracts.graph import GraphSnapshotV3
from ai_canvas.domain.agent.scope import slice_utf16
from ai_canvas.domain.control.canonical import canonical_json


_scope_adapter = TypeAdapter(AgentScope)
_CONTEXT_LIMITS = {
    "USER_INSTRUCTION": 20_000,
    "SELECTED_NODE_TEXT": 20_000,
    "SELECTED_TEXT": 10_000,
    "ADJACENT_NODE_TEXT": 10_000,
    "RESOURCE_TEXT_METADATA": 10_000,
}


@dataclass(frozen=True)
class CompiledAgentPayload:
    canonical_bytes: bytes
    payload_sha256: str
    provider_payload: dict[str, Any]
    preview: dict[str, Any]


def _bounded_context(category: str, content: str) -> dict[str, Any]:
    limit = _CONTEXT_LIMITS[category]
    bounded = content[:limit]
    return {
        "category": category,
        "content": bounded,
        "original_characters": len(content),
        "sent_characters": len(bounded),
        "truncated": len(bounded) != len(content),
    }


def _output_schema() -> dict[str, Any]:
    replacement_operation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "node_id", "replacement_text"],
        "properties": {
            "op": {"const": "replace_text"},
            "node_id": {"type": "string", "format": "uuid"},
            "replacement_text": {"type": "string", "maxLength": 100_000},
        },
    }
    return {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "schema_version", "operation", "summary"],
                "properties": {
                    "kind": {"const": "graph_change_set_draft"},
                    "schema_version": {"const": 1},
                    "operation": replacement_operation,
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "schema_version",
                    "operation_kind",
                    "target_node_ids",
                    "task_count",
                    "reason",
                ],
                "properties": {
                    "kind": {"const": "run_request_draft"},
                    "schema_version": {"const": 1},
                    "operation_kind": {"const": "DRY_RUN"},
                    "target_node_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {"type": "string", "format": "uuid"},
                    },
                    "task_count": {"const": 1},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        ]
    }


def compile_agent_payload(
    *,
    graph: GraphSnapshotV3,
    node_id: UUID,
    scope: AgentScope | dict,
    instruction: str,
    model_id: str,
    requested_categories: list[str],
    allowed_categories: list[str],
    adjacent_node_texts: list[str] | None = None,
    resource_text_metadata: list[dict[str, str]] | None = None,
) -> CompiledAgentPayload:
    if len(set(requested_categories)) != len(requested_categories):
        raise ValueError("requested context categories must be unique")
    forbidden = set(requested_categories) - set(allowed_categories)
    if forbidden:
        raise ValueError("requested context category is not allowed by profile")
    if "USER_INSTRUCTION" not in requested_categories:
        raise ValueError("USER_INSTRUCTION context is required")
    validated_scope = _scope_adapter.validate_python(scope)
    node = next((item for item in graph.nodes if item.id == node_id), None)
    if node is None:
        raise ValueError("Agent target node does not exist")
    if node.kind in {"text_source", "note"}:
        node_text = node.data.text
    elif node.kind == "prompt":
        node_text = node.data.document.rendered_text
    else:
        raise ValueError("Agent target must be a text-bearing node")
    selected_text = (
        node_text
        if validated_scope.kind == "NODE"
        else slice_utf16(
            node_text,
            validated_scope.start_utf16,
            validated_scope.end_utf16,
        )
    )
    adjacent_content = canonical_json(adjacent_node_texts or [])
    resource_content = canonical_json(resource_text_metadata or [])
    sources = {
        "USER_INSTRUCTION": instruction,
        "SELECTED_NODE_TEXT": node_text,
        "SELECTED_TEXT": selected_text,
        "ADJACENT_NODE_TEXT": adjacent_content,
        "RESOURCE_TEXT_METADATA": resource_content,
    }
    contexts = [_bounded_context(category, sources[category]) for category in requested_categories]
    provider_payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object that matches the supplied output schema. "
                    "Do not request tools or external capabilities."
                ),
            },
            {
                "role": "user",
                "content": {
                    "target_node_id": str(node_id),
                    "scope": validated_scope.model_dump(mode="json"),
                    "contexts": contexts,
                },
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "agent_output_v1",
                "strict": True,
                "schema": _output_schema(),
            },
        },
        "stream": False,
    }
    encoded = canonical_json(provider_payload).encode("utf-8")
    preview = {
        "contexts": contexts,
        "output_kinds": ["graph_change_set_draft", "run_request_draft"],
        "redactions": [
            "credentials_not_in_payload",
            "paths_logs_environment_not_collected",
        ],
    }
    return CompiledAgentPayload(
        canonical_bytes=encoded,
        payload_sha256=sha256(encoded).hexdigest(),
        provider_payload=provider_payload,
        preview=preview,
    )
