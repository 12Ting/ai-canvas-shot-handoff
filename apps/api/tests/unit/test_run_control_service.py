import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event as sqlalchemy_event, select, text
from sqlalchemy.exc import IntegrityError

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.run_control_service import (
    RunAuthorizationConsumed,
    RunControlService,
    utc_now,
)
from ai_canvas.contracts.run_control import CreateRunRequest
from ai_canvas.contracts.config_changesets import CreateConfigChangeSetRequest
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.db.models import (
    ControlConfigRevisionRow,
    DryRunReceiptRow,
    EgressPlanRow,
    GraphRevisionRow,
    RunAuthorizationRow,
    RunRequestRow,
)


def test_no_egress_dry_run_consumes_its_authorization_once(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 dry run")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        authorization = service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )

        receipt = service.dry_run(project.id, request.id, actor_id="local-session")

        assert authorization.state == "APPROVED"
        assert receipt.terminal_state == "DRY_RUN_SUCCEEDED"
        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")
        chain = events.query(project.id, after=0, limit=10, correlation_id=request.correlation_id)
        assert [event.event_type for event in chain.events] == [
            "run_request.requested",
            "egress_plan.sealed",
            "approval.recorded",
            "run_authorization.issued",
            "run_authorization.consumed",
            "run.dry_run_succeeded",
        ]
    finally:
        engine.dispose()


def test_expected_dry_run_validation_failure_persists_unique_terminal_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("dry-run validation failure")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        monkeypatch.setattr(
            service,
            "_dry_run_validation_error",
            lambda *_args: "INJECTED_VALIDATION_FAILURE",
        )

        with pytest.raises(Exception) as captured:
            service.dry_run(project.id, request.id, actor_id="local-session")
        assert type(captured.value).__name__ == "DryRunValidationFailed"

        with session_factory() as session:
            receipts = list(
                session.scalars(
                    select(DryRunReceiptRow).where(DryRunReceiptRow.run_request_id == str(request.id))
                )
            )
            assert len(receipts) == 1
            assert receipts[0].terminal_state == "DRY_RUN_VALIDATION_FAILED"
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "CONSUMED"
        chain = events.query(
            project.id,
            after=0,
            limit=20,
            correlation_id=request.correlation_id,
        )
        assert [event.event_type for event in chain.events][-2:] == [
            "run_authorization.consumed",
            "run.dry_run_failed",
        ]
        assert chain.events[-1].payload["reason"] == "INJECTED_VALIDATION_FAILURE"
    finally:
        engine.dispose()


def test_run_approval_rejects_a_changed_sealed_egress_plan(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 plan integrity")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            session.execute(text("DROP TRIGGER f1_egress_plans_update_immutable"))
            plan = session.scalar(
                select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(request.id))
            )
            assert plan is not None
            plan.task_count = 99

        with pytest.raises(ValueError, match="integrity"):
            service.approve(
                project.id,
                request.id,
                expected_request_sha256=request.request_sha256,
                actor_id="local-session",
            )

        with session_factory() as session:
            assert (
                session.scalar(
                    select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
                )
                is None
            )
        chain = events.query(project.id, after=0, limit=10, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_request.rejected"
        assert chain.events[-1].payload["reason"] == "SUBJECT_INTEGRITY_MISMATCH"
    finally:
        engine.dispose()


def test_dry_run_consumes_authorization_with_a_conditional_database_update(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 atomic consume")
        service = RunControlService(session_factory, EventSpineService(session_factory))
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        statements: list[str] = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
            if statement.lstrip().upper().startswith("UPDATE RUN_AUTHORIZATIONS"):
                statements.append(" ".join(statement.lower().split()))

        sqlalchemy_event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            service.dry_run(project.id, request.id, actor_id="local-session")
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", capture_statement)

        assert len(statements) == 1
        assert "run_authorizations.state =" in statements[0].split(" where ", 1)[1]
        assert "run_authorizations.request_sha256 =" in statements[0].split(" where ", 1)[1]
        assert "run_authorizations.egress_plan_sha256 =" in statements[0].split(" where ", 1)[1]
    finally:
        engine.dispose()


def test_expired_authorization_persists_terminal_event_before_rejection(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("expired authorization")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            # Emulate a legacy/tampered database so the service-level expiry
            # guard is exercised independently of the immutable binding trigger.
            session.execute(text("DROP TRIGGER f1_run_authorizations_binding_immutable"))
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            authorization.expires_at = utc_now() - timedelta(seconds=1)

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "EXPIRED"
        chain = events.query(project.id, after=0, limit=10, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.expired"
    finally:
        engine.dispose()


def test_changed_control_config_invalidates_authorization_before_dry_run(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("invalidated authorization")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        control = ControlConfigService(session_factory, events)
        current_config = control.get_active()
        change = control.request(
            CreateConfigChangeSetRequest.model_validate(
                {
                    "kind": "config_change_set",
                    "schema_version": 1,
                    "audit_project_id": str(project.id),
                    "base_config_revision_id": str(current_config.id),
                    "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
                }
            ),
            actor_id="local-control-admin",
        )
        control.approve(
            change.id,
            expected_changeset_sha256=change.changeset_sha256,
            actor_id="local-control-admin",
        )
        control.apply(
            change.id,
            expected_changeset_sha256=change.changeset_sha256,
            actor_id="local-control-admin",
        )

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
        chain = events.query(project.id, after=0, limit=20, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()


def test_changed_graph_revision_invalidates_authorization_before_dry_run(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("graph invalidated authorization")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        graphs = GraphChangeSetService(session_factory, GraphService(session_factory), events)
        change = graphs.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": str(project.active_revision_id),
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
            ),
            actor_id="local-session",
        )
        graphs.approve(
            project.id,
            change.id,
            expected_changeset_sha256=change.changeset_sha256,
            actor_id="local-session",
        )
        graphs.apply(
            project.id,
            change.id,
            expected_changeset_sha256=change.changeset_sha256,
            actor_id="local-session",
        )

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
        chain = events.query(project.id, after=0, limit=20, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()


def test_changed_egress_plan_invalidates_authorization_before_dry_run(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("plan invalidated authorization")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            # Emulate a legacy/tampered database and verify defense in depth.
            session.execute(text("DROP TRIGGER f1_egress_plans_update_immutable"))
            plan = session.scalar(
                select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(request.id))
            )
            assert plan is not None
            plan.plan_sha256 = "f" * 64

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
        chain = events.query(project.id, after=0, limit=20, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()


def test_changed_egress_plan_content_invalidates_authorization_before_dry_run(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("plan content invalidation")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            # Emulate a legacy/tampered database and verify defense in depth.
            session.execute(text("DROP TRIGGER f1_egress_plans_update_immutable"))
            plan = session.scalar(
                select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(request.id))
            )
            assert plan is not None
            plan.data_classes_json = '["external"]'

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
        chain = events.query(project.id, after=0, limit=20, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()


def test_changed_request_content_invalidates_authorization_before_dry_run(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("request content invalidation")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            # Emulate a legacy/tampered database and verify defense in depth.
            session.execute(text("DROP TRIGGER f1_run_requests_content_immutable"))
            stored_request = session.get(RunRequestRow, str(request.id))
            assert stored_request is not None
            body = json.loads(stored_request.request_json)
            body["task_count"] = 2
            stored_request.request_json = json.dumps(body, sort_keys=True, separators=(",", ":"))

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
        chain = events.query(project.id, after=0, limit=20, correlation_id=request.correlation_id)
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()


def test_graph_and_config_revision_content_is_database_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("immutable run inputs")
        events = EventSpineService(session_factory)
        request = RunControlService(session_factory, events).request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        assert request.state == "PENDING_APPROVAL"
        with pytest.raises(IntegrityError, match="graph revisions are immutable"):
            with session_factory.begin() as session:
                graph_revision = session.get(GraphRevisionRow, str(project.active_revision_id))
                assert graph_revision is not None
                graph_revision.snapshot_hash = "f" * 64

        with pytest.raises(IntegrityError, match="control config revisions are immutable"):
            with session_factory.begin() as session:
                config_revision = session.scalar(select(ControlConfigRevisionRow))
                assert config_revision is not None
                config_revision.config_sha256 = "f" * 64
    finally:
        engine.dispose()


@pytest.mark.parametrize("domain", ["graph", "config"])
def test_changed_revision_content_invalidates_authorization_before_dry_run(
    tmp_path: Path,
    domain: str,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create(f"changed {domain} revision")
        events = EventSpineService(session_factory)
        service = RunControlService(session_factory, events)
        request = service.request(
            project.id,
            CreateRunRequest(operation_kind="DRY_RUN", task_count=1),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            if domain == "graph":
                session.execute(text("DROP TRIGGER f1_graph_revisions_content_immutable"))
                revision = session.get(GraphRevisionRow, str(project.active_revision_id))
                assert revision is not None
                revision.snapshot_json = '{"schema_version":2,"nodes":[],"edges":[],"shots":[]}'
                revision.snapshot_hash = "f" * 64
            else:
                session.execute(text("DROP TRIGGER f1_control_config_revisions_content_immutable"))
                revision = session.scalar(select(ControlConfigRevisionRow))
                assert revision is not None
                revision.config_json = '{"dry_run_max_tasks":0}'
                revision.config_sha256 = "f" * 64

        with pytest.raises(RunAuthorizationConsumed):
            service.dry_run(project.id, request.id, actor_id="local-session")

        with session_factory() as session:
            authorization = session.scalar(
                select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request.id))
            )
            assert authorization is not None
            assert authorization.state == "INVALIDATED"
            assert (
                session.scalar(select(RunRequestRow).where(RunRequestRow.id == str(request.id))).state
                == "TERMINAL"
            )
        chain = events.query(
            project.id,
            after=0,
            limit=20,
            correlation_id=request.correlation_id,
        )
        assert chain.events[-1].event_type == "run_authorization.invalidated"
    finally:
        engine.dispose()
