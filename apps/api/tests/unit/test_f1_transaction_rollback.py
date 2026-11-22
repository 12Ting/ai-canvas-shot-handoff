from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.contracts.config_changesets import CreateConfigChangeSetRequest
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.contracts.run_control import CreateRunRequest
from ai_canvas.infrastructure.db.models import (
    ConfigChangeSetRow,
    DryRunReceiptRow,
    GraphChangeSetRow,
    RunAuthorizationRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


class FailOnEvent:
    def __init__(self, delegate: EventSpineService, event_type: str) -> None:
        self.delegate = delegate
        self.event_type = event_type

    def append_in_session(self, session, **kwargs):
        if kwargs["event_type"] == self.event_type:
            raise RuntimeError(f"forced event append failure: {self.event_type}")
        return self.delegate.append_in_session(session, **kwargs)


def _graph_request(base_revision_id):
    return CreateGraphChangeSetRequest.model_validate(
        {
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": str(base_revision_id),
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
    )


def test_graph_apply_rolls_back_revision_when_terminal_event_append_fails(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("Graph event rollback")
        events = EventSpineService(session_factory)
        graph_service = GraphService(session_factory)
        service = GraphChangeSetService(session_factory, graph_service, events)
        proposal = service.request(project.id, _graph_request(project.active_revision_id), actor_id="human")
        service.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="human",
        )
        service.events = FailOnEvent(events, "graph_change_set.applied")

        with pytest.raises(RuntimeError, match="forced event append failure"):
            service.apply(
                project.id,
                proposal.id,
                expected_changeset_sha256=proposal.changeset_sha256,
                actor_id="human",
            )

        assert graph_service.get_active(project.id).revision_id == project.active_revision_id
        with session_factory() as session:
            row = session.get(GraphChangeSetRow, str(proposal.id))
            assert row is not None
            assert row.state == "APPROVED"
            assert row.applied_graph_revision_id is None
    finally:
        engine.dispose()


def test_config_apply_rolls_back_revision_when_terminal_event_append_fails(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("Config event rollback")
        events = EventSpineService(session_factory)
        service = ControlConfigService(session_factory, events)
        before = service.get_active()
        proposal = service.request(
            CreateConfigChangeSetRequest.model_validate(
                {
                    "kind": "config_change_set",
                    "schema_version": 1,
                    "audit_project_id": str(project.id),
                    "base_config_revision_id": str(before.id),
                    "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
                }
            ),
            actor_id="control-admin",
        )
        service.approve(
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="control-admin",
        )
        service.events = FailOnEvent(events, "config_change_set.applied")

        with pytest.raises(RuntimeError, match="forced event append failure"):
            service.apply(
                proposal.id,
                expected_changeset_sha256=proposal.changeset_sha256,
                actor_id="control-admin",
            )

        assert service.get_active().id == before.id
        with session_factory() as session:
            row = session.get(ConfigChangeSetRow, str(proposal.id))
            assert row is not None
            assert row.state == "APPROVED"
            assert row.applied_config_revision_id is None
    finally:
        engine.dispose()


def test_run_approval_rolls_back_authorization_when_issue_event_append_fails(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("Authorization rollback")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="human",
        )
        service.events = FailOnEvent(events, "run_authorization.issued")

        with pytest.raises(RuntimeError, match="forced event append failure"):
            service.approve(
                project.id,
                request.id,
                expected_request_sha256=request.request_sha256,
                actor_id="human",
            )

        with session_factory() as session:
            row = session.get(RunRequestRow, str(request.id))
            assert row is not None
            assert row.state == "PENDING_APPROVAL"
            assert row.approval_id is None
            assert session.scalar(select(func.count()).select_from(RunAuthorizationRow)) == 0
    finally:
        engine.dispose()


def test_dry_run_rolls_back_consumption_and_receipt_when_terminal_event_fails(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("Receipt rollback")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="human",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="human",
        )
        service.events = FailOnEvent(events, "run.dry_run_succeeded")

        with pytest.raises(RuntimeError, match="forced event append failure"):
            service.dry_run(project.id, request.id, actor_id="human")

        with session_factory() as session:
            request_row = session.get(RunRequestRow, str(request.id))
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert request_row is not None
            assert authorization is not None
            assert request_row.state == "APPROVED"
            assert authorization.state == "APPROVED"
            assert authorization.consumed_at is None
            assert session.scalar(select(func.count()).select_from(DryRunReceiptRow)) == 0
    finally:
        engine.dispose()
