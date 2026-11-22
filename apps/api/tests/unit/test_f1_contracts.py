from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_canvas.contracts.config_changesets import CreateConfigChangeSetRequest
from ai_canvas.contracts.experience_events import (
    EventCursorAckRequest,
    ExperienceEventResponse,
)
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest


def _graph_payload() -> dict[str, object]:
    return {
        "kind": "graph_change_set",
        "schema_version": 1,
        "base_graph_revision_id": str(uuid4()),
        "client_mutation_id": str(uuid4()),
        "operations": [
            {
                "op": "replace_snapshot",
                "snapshot": {
                    "schema_version": 2,
                    "nodes": [],
                    "edges": [],
                    "shots": [],
                },
            }
        ],
    }


def _config_payload() -> dict[str, object]:
    return {
        "kind": "config_change_set",
        "schema_version": 1,
        "audit_project_id": str(uuid4()),
        "base_config_revision_id": str(uuid4()),
        "operations": [
            {
                "op": "replace",
                "path": "/dry_run_max_tasks",
                "value": 2,
            }
        ],
    }


def test_graph_and_config_change_sets_are_strictly_disjoint() -> None:
    graph = CreateGraphChangeSetRequest.model_validate(_graph_payload())
    config = CreateConfigChangeSetRequest.model_validate(_config_payload())

    assert graph.kind == "graph_change_set"
    assert config.kind == "config_change_set"

    with pytest.raises(ValidationError):
        CreateGraphChangeSetRequest.model_validate(_config_payload())
    with pytest.raises(ValidationError):
        CreateConfigChangeSetRequest.model_validate(_graph_payload())


@pytest.mark.parametrize(
    "payload",
    [
        {**_graph_payload(), "extra": True},
        {
            **_config_payload(),
            "operations": [
                {"op": "replace", "path": "/provider_endpoint", "value": "https://bad.invalid"}
            ],
        },
        {
            **_config_payload(),
            "operations": [
                {"op": "replace", "path": "/dry_run_max_tasks", "value": 101}
            ],
        },
    ],
)
def test_f1_change_set_contracts_reject_unknown_or_out_of_scope_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        if payload["kind"] == "graph_change_set":
            CreateGraphChangeSetRequest.model_validate(payload)
        else:
            CreateConfigChangeSetRequest.model_validate(payload)


def test_event_envelope_and_cursor_contracts_are_bounded_and_strict() -> None:
    event = ExperienceEventResponse.model_validate(
        {
            "event_id": str(uuid4()),
            "stream_position": 1,
            "occurred_at": "2026-08-27T00:00:00Z",
            "project_id": str(uuid4()),
            "correlation_id": str(uuid4()),
            "causation_event_id": None,
            "actor_kind": "HUMAN",
            "actor_id": "local-session",
            "subject_type": "graph_change_set",
            "subject_id": str(uuid4()),
            "event_type": "graph_change_set.requested",
            "schema_version": 1,
            "payload": {"changeset_sha256": "a" * 64},
            "payload_sha256": "a" * 64,
            "envelope_sha256": "b" * 64,
        }
    )
    assert event.stream_position == 1
    assert EventCursorAckRequest(ack_stream_position=1).ack_stream_position == 1

    with pytest.raises(ValidationError):
        ExperienceEventResponse.model_validate({**event.model_dump(), "stream_position": 0})
    with pytest.raises(ValidationError):
        EventCursorAckRequest(ack_stream_position=-1)
    with pytest.raises(ValidationError):
        ExperienceEventResponse.model_validate({**event.model_dump(), "event_type": "anything"})
