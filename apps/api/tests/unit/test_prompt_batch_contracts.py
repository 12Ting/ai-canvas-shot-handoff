from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_canvas.contracts.prompt_batches import (
    AllShotsBatchSelection,
    CreatePromptBatchRequest,
    PromptBatchItemDecisionRequest,
    PromptBatchItemDraftV1,
    PromptBatchRetryRequest,
    SelectedShotsBatchSelection,
)


def create_request(**overrides: object) -> dict[str, object]:
    branch_id = uuid4()
    revision_id = uuid4()
    body: dict[str, object] = {
        "base_graph_revision_id": revision_id,
        "selection": {
            "mode": "SELECTED",
            "shot_ids": [uuid4(), uuid4()],
        },
        "prompt_profile_binding_key": "prompt_profile",
        "application_target": {
            "mode": "NEW_BRANCH",
            "base_branch_id": branch_id,
            "branch_name": "C4 批量候选",
            "expected_branch_head_revision_id": revision_id,
            "expected_branch_head_version": 2,
            "expected_selection_version": 3,
        },
        "instruction": "按已固定 PromptProfile 统一可编辑结构块",
        "client_mutation_id": uuid4(),
    }
    body.update(overrides)
    return body


def test_batch_selection_is_strict_and_selected_ids_are_unique() -> None:
    shot_id = uuid4()
    selection = SelectedShotsBatchSelection(shot_ids=[shot_id])

    assert selection.mode == "SELECTED"
    assert AllShotsBatchSelection().mode == "ALL"
    with pytest.raises(ValidationError):
        SelectedShotsBatchSelection(shot_ids=[shot_id, shot_id])
    with pytest.raises(ValidationError):
        SelectedShotsBatchSelection(shot_ids=[uuid4() for _ in range(101)])
    with pytest.raises(ValidationError):
        AllShotsBatchSelection(shot_ids=[shot_id])


def test_create_batch_requires_exact_branch_target_and_forbids_extra_fields() -> None:
    request = CreatePromptBatchRequest.model_validate(create_request())

    assert request.selection.mode == "SELECTED"
    assert request.application_target.mode == "NEW_BRANCH"
    with pytest.raises(ValidationError):
        CreatePromptBatchRequest.model_validate(create_request(extra=True))
    with pytest.raises(ValidationError):
        CreatePromptBatchRequest.model_validate(
            create_request(application_target={"mode": "NEW_BRANCH"})
        )


def test_item_draft_binds_exact_subject_and_unique_replacements() -> None:
    block_id = uuid4()
    payload = {
        "kind": "prompt_batch_item_draft",
        "schema_version": 1,
        "shot_id": uuid4(),
        "prompt_node_id": uuid4(),
        "base_document_sha256": "a" * 64,
        "replacements": [
            {"block_id": block_id, "replacement_text": "中景，低机位"}
        ],
        "summary": "统一镜头表达",
    }
    draft = PromptBatchItemDraftV1.model_validate(payload)

    assert draft.replacements[0].block_id == block_id
    with pytest.raises(ValidationError):
        PromptBatchItemDraftV1.model_validate(
            {
                **payload,
                "replacements": [
                    {"block_id": block_id, "replacement_text": "A"},
                    {"block_id": block_id, "replacement_text": "B"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        PromptBatchItemDraftV1.model_validate(
            {**payload, "base_document_sha256": "not-a-hash"}
        )


def test_decision_and_retry_requests_require_exact_versions_and_failed_subset() -> None:
    decision = PromptBatchItemDecisionRequest(
        decision="ACCEPTED",
        attempt_id=uuid4(),
        expected_output_sha256="b" * 64,
        expected_decision_version=1,
        client_mutation_id=uuid4(),
    )
    retry = PromptBatchRetryRequest(
        failed_item_ids=[uuid4()],
        expected_batch_version=2,
        client_mutation_id=uuid4(),
    )

    assert decision.expected_decision_version == 1
    assert retry.failed_item_ids
    with pytest.raises(ValidationError):
        PromptBatchRetryRequest(
            failed_item_ids=[],
            expected_batch_version=2,
            client_mutation_id=uuid4(),
        )
    with pytest.raises(ValidationError):
        PromptBatchRetryRequest(
            failed_item_ids=[retry.failed_item_ids[0], retry.failed_item_ids[0]],
            expected_batch_version=2,
            client_mutation_id=uuid4(),
        )
    with pytest.raises(ValidationError):
        PromptBatchRetryRequest.model_validate(
            {
                "unknown_item_ids": [uuid4()],
                "expected_batch_version": 2,
                "client_mutation_id": uuid4(),
            }
        )
