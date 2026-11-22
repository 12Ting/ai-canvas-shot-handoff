from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.models import GraphChangeSetRow
from ai_canvas.main import create_app


def _headers(app, client: TestClient) -> dict[str, str]:
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    return {
        "Authorization": f"Bearer {exchanged.json()['access_token']}",
        "Origin": "http://testserver",
    }


def test_direct_undo_creates_and_applies_a_new_changeset_revision(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _headers(app, client)
        project = client.post(
            "/api/v1/projects", headers=headers, json={"name": "Undo"}
        ).json()
        workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        main = workspace["branches"][0]
        proposal = app.state.graph_changeset_service.request(
            project["id"],
            CreateGraphChangeSetRequest.model_validate(
                {
                    "base_graph_revision_id": project["active_revision_id"],
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
                    "application_target": {
                        "mode": "DIRECT",
                        "branch_id": main["id"],
                        "expected_branch_head_revision_id": main["head"][
                            "graph_revision_id"
                        ],
                        "expected_branch_head_version": main["head"]["version"],
                        "expected_selection_version": workspace["selection"]["version"],
                    },
                }
            ),
            actor_id="local-session",
        )
        app.state.graph_changeset_service.approve(
            project["id"],
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        applied = app.state.graph_changeset_service.apply(
            project["id"],
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        undo = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal.id}/undo-proposals",
            headers=headers,
            json={"client_mutation_id": str(uuid4())},
        )
        assert undo.status_code == 201, undo.text
        undo_proposal = undo.json()
        assert undo_proposal["id"] != str(proposal.id)
        assert undo_proposal["base_graph_revision_id"] == str(
            applied.applied_graph_revision_id
        )
        approved = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{undo_proposal['id']}/approval-decisions",
            headers=headers,
            json={
                "expected_changeset_sha256": undo_proposal["changeset_sha256"],
                "decision": "APPROVED",
            },
        )
        assert approved.status_code == 200, approved.text
        undone = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{undo_proposal['id']}/apply",
            headers=headers,
            json={
                "expected_changeset_sha256": undo_proposal["changeset_sha256"],
                "decision": "APPROVED",
            },
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["applied_graph_revision_id"] not in {
            project["active_revision_id"],
            str(applied.applied_graph_revision_id),
        }
        stale = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal.id}/undo-proposals",
            headers=headers,
            json={"client_mutation_id": str(uuid4())},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json() == {"code": "graph_changeset_undo_stale"}


def test_pre_c2_graph_changeset_undo_is_explicitly_unsupported(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _headers(app, client)
        project = client.post(
            "/api/v1/projects", headers=headers, json={"name": "Historical undo"}
        ).json()
        historical_id = str(uuid4())
        with app.state.graph_service.session_factory.begin() as session:
            session.add(
                GraphChangeSetRow(
                    id=historical_id,
                    project_id=project["id"],
                    correlation_id=str(uuid4()),
                    base_graph_revision_id=project["active_revision_id"],
                    client_mutation_id=str(uuid4()),
                    snapshot_json='{"edges":[],"nodes":[],"schema_version":2,"shots":[]}',
                    snapshot_hash="a" * 64,
                    diff_json='{"op":"replace_snapshot"}',
                    inverse_operations_json="[]",
                    risk_json='{"classes":["GRAPH_WRITE"]}',
                    changeset_sha256="b" * 64,
                    state="APPLIED",
                    approval_id=None,
                    applied_graph_revision_id=project["active_revision_id"],
                    application_target_json=None,
                    impact_json=None,
                    impact_sha256=None,
                    created_branch_id=None,
                    applied_branch_id=None,
                    created_at=datetime.now(timezone.utc),
                    applied_at=datetime.now(timezone.utc),
                )
            )

        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{historical_id}/undo-proposals",
            headers=headers,
            json={"client_mutation_id": str(uuid4())},
        )
        assert response.status_code == 409, response.text
        assert response.json() == {"code": "graph_changeset_undo_unsupported"}
