from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_canvas.contracts.agent_proposals import (
    AgentGraphChangeSetDraftV1,
    AgentOutputDraft,
    AgentRunRequestDraftV1,
    CreateAgentProposalRequest,
)
from ai_canvas.contracts.graph import GraphSnapshotV3, Point, TextSourceNodeV3
from ai_canvas.domain.agent.payload import compile_agent_payload


output_adapter = TypeAdapter(AgentOutputDraft)


def test_agent_request_binds_one_node_or_utf16_selection_and_c2_cas() -> None:
    project_node_id = uuid4()
    revision_id = uuid4()
    branch_id = uuid4()
    request = CreateAgentProposalRequest(
        base_graph_revision_id=revision_id,
        branch_id=branch_id,
        expected_branch_head_revision_id=revision_id,
        expected_branch_head_version=2,
        expected_selection_version=3,
        node_id=project_node_id,
        scope={"kind": "TEXT_SELECTION", "start_utf16": 2, "end_utf16": 5},
        instruction="只改写选中部分",
        context_categories=["USER_INSTRUCTION", "SELECTED_TEXT"],
        new_branch_name="AI 单次提案",
    )

    assert request.node_id == project_node_id
    assert request.scope.kind == "TEXT_SELECTION"
    assert request.scope.end_utf16 == 5


def test_agent_request_rejects_reversed_selection_and_unknown_context() -> None:
    body = {
        "base_graph_revision_id": str(uuid4()),
        "branch_id": str(uuid4()),
        "expected_branch_head_revision_id": str(uuid4()),
        "expected_branch_head_version": 1,
        "expected_selection_version": 1,
        "node_id": str(uuid4()),
        "scope": {"kind": "TEXT_SELECTION", "start_utf16": 5, "end_utf16": 2},
        "instruction": "rewrite",
        "context_categories": ["FULL_PROJECT_FILES"],
        "new_branch_name": "proposal",
    }

    with pytest.raises(ValidationError):
        CreateAgentProposalRequest.model_validate(body)


def test_model_output_accepts_exactly_graph_or_non_executable_run_draft() -> None:
    node_id = uuid4()
    graph = output_adapter.validate_python(
        {
            "kind": "graph_change_set_draft",
            "schema_version": 1,
            "operation": {
                "op": "replace_text",
                "node_id": str(node_id),
                "replacement_text": "新文本",
            },
            "summary": "只更新选中文字",
        }
    )
    run = output_adapter.validate_python(
        {
            "kind": "run_request_draft",
            "schema_version": 1,
            "operation_kind": "DRY_RUN",
            "target_node_ids": [str(node_id)],
            "task_count": 1,
            "reason": "建议先做无外发验证",
        }
    )

    assert isinstance(graph, AgentGraphChangeSetDraftV1)
    assert isinstance(run, AgentRunRequestDraftV1)


@pytest.mark.parametrize(
    "body",
    [
        {
            "kind": "config_change_set",
            "schema_version": 1,
            "operations": [],
        },
        {
            "kind": "graph_change_set_draft",
            "schema_version": 1,
            "operation": {
                "op": "replace_text",
                "node_id": str(uuid4()),
                "replacement_text": "x",
                "config_route": "/api/v1/control/config-change-sets",
            },
            "summary": "x",
        },
        {
            "kind": "run_request_draft",
            "schema_version": 1,
            "operation_kind": "MEDIA_RENDER",
            "target_node_ids": [str(uuid4())],
            "task_count": 1,
            "reason": "x",
        },
        {
            "kind": "run_request_draft",
            "schema_version": 1,
            "operation_kind": "DRY_RUN",
            "target_node_ids": [str(uuid4()), str(uuid4())],
            "task_count": 2,
            "reason": "x",
        },
    ],
)
def test_model_output_rejects_config_unknown_fields_media_and_batch(
    body: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        output_adapter.validate_python(body)


def test_provider_payload_is_deterministic_minimal_and_credential_free() -> None:
    node_id = uuid4()
    graph = GraphSnapshotV3(
        nodes=[
            TextSourceNodeV3(
                id=node_id,
                kind="text_source",
                position=Point(x=1, y=2),
                data={"text": "前🙂选中后"},
            )
        ]
    )
    arguments = {
        "graph": graph,
        "node_id": node_id,
        "scope": {"kind": "TEXT_SELECTION", "start_utf16": 3, "end_utf16": 5},
        "instruction": "只改写选中文字",
        "model_id": "loopback-c3",
        "requested_categories": ["USER_INSTRUCTION", "SELECTED_TEXT"],
        "allowed_categories": [
            "USER_INSTRUCTION",
            "SELECTED_TEXT",
            "SELECTED_NODE_TEXT",
        ],
    }
    first = compile_agent_payload(**arguments)
    second = compile_agent_payload(**arguments)

    assert first.canonical_bytes == second.canonical_bytes
    assert first.payload_sha256 == second.payload_sha256
    assert [item["category"] for item in first.preview["contexts"]] == [
        "USER_INSTRUCTION",
        "SELECTED_TEXT",
    ]
    assert first.preview["contexts"][1]["content"] == "选中"
    forbidden = (
        "authorization",
        "api_key",
        "credential",
        "environment",
        "file_path",
        "logs",
        "config_change_set",
    )
    lowered = first.canonical_bytes.decode("utf-8").lower()
    assert all(value not in lowered for value in forbidden)
    assert first.preview["redactions"] == [
        "credentials_not_in_payload",
        "paths_logs_environment_not_collected",
    ]


def test_payload_enforces_profile_categories_and_records_truncation() -> None:
    node_id = uuid4()
    graph = GraphSnapshotV3(
        nodes=[
            TextSourceNodeV3(
                id=node_id,
                kind="text_source",
                position=Point(x=0, y=0),
                data={"text": "字" * 30_000},
            )
        ]
    )
    compiled = compile_agent_payload(
        graph=graph,
        node_id=node_id,
        scope={"kind": "NODE"},
        instruction="改写",
        model_id="loopback-c3",
        requested_categories=["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
        allowed_categories=["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
    )
    selected = compiled.preview["contexts"][1]
    assert selected["truncated"] is True
    assert selected["original_characters"] == 30_000
    assert selected["sent_characters"] == 20_000

    with pytest.raises(ValueError, match="not allowed"):
        compile_agent_payload(
            graph=graph,
            node_id=node_id,
            scope={"kind": "NODE"},
            instruction="改写",
            model_id="loopback-c3",
            requested_categories=["USER_INSTRUCTION", "ADJACENT_NODE_TEXT"],
            allowed_categories=["USER_INSTRUCTION"],
        )
