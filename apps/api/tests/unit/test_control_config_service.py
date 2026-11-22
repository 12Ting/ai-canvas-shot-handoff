from pathlib import Path

import pytest
from sqlalchemy import text

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.contracts.config_changesets import CreateConfigChangeSetRequest
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.db.models import ConfigChangeSetRow


def test_config_change_set_creates_new_control_revision_and_event_chain(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 config control")
        events = EventSpineService(session_factory)
        service = ControlConfigService(session_factory, events)
        before = service.get_active()
        graph_before = GraphService(session_factory).get_active(project.id).revision_id

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
            actor_id="local-control-admin",
        )
        approved = service.approve(
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-control-admin",
        )
        applied = service.apply(
            approved.id,
            expected_changeset_sha256=approved.changeset_sha256,
            actor_id="local-control-admin",
        )

        assert applied.state == "APPLIED"
        assert service.get_active().dry_run_max_tasks == 2
        assert GraphService(session_factory).get_active(project.id).revision_id == graph_before
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert [event.event_type for event in chain.events] == [
            "config_change_set.requested",
            "approval.recorded",
            "config_change_set.applied",
        ]
    finally:
        engine.dispose()


def test_config_apply_rejects_operations_changed_after_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 config integrity")
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
            actor_id="local-control-admin",
        )
        service.approve(
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-control-admin",
        )
        with session_factory.begin() as session:
            session.execute(text("DROP TRIGGER f1_config_change_sets_content_immutable"))
            row = session.get(ConfigChangeSetRow, str(proposal.id))
            assert row is not None
            row.operations_json = '[{"op":"replace","path":"/dry_run_max_tasks","value":99}]'

        with pytest.raises(ValueError, match="integrity"):
            service.apply(
                proposal.id,
                expected_changeset_sha256=proposal.changeset_sha256,
                actor_id="local-control-admin",
            )

        assert service.get_active().id == before.id
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert chain.events[-1].event_type == "config_change_set.rejected"
        assert chain.events[-1].payload["reason"] == "SUBJECT_INTEGRITY_MISMATCH"
    finally:
        engine.dispose()
