from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ai_canvas.config import Settings
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.models import GraphPromotionRow, GraphRevisionRow
from ai_canvas.main import create_app


def _headers(app, client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert response.status_code == 200
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "Origin": "http://testserver",
    }


def test_promotion_moves_only_selection_and_active_pointer(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        headers = _headers(app, client)
        project = client.post(
            "/api/v1/projects", headers=headers, json={"name": "Promotion"}
        ).json()
        workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        main = workspace["branches"][0]
        proposal = app.state.graph_changeset_service.request(
            project_id=project["id"],
            request=CreateGraphChangeSetRequest.model_validate(
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
                        "mode": "NEW_BRANCH",
                        "base_branch_id": main["id"],
                        "branch_name": "待提升",
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
            project_id=project["id"],
            change_set_id=proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        experiment = app.state.graph_changeset_service.apply(
            project_id=project["id"],
            change_set_id=proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        refreshed = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        target = next(
            branch
            for branch in refreshed["branches"]
            if branch["id"] == str(experiment.created_branch_id)
        )
        stale_request = client.post(
            f"/api/v1/projects/{project['id']}/promotions",
            headers=headers,
            json={
                "client_mutation_id": str(uuid4()),
                "target_branch_id": target["id"],
                "expected_target_branch_head_revision_id": target["head"][
                    "graph_revision_id"
                ],
                "expected_target_branch_head_version": target["head"]["version"],
                "expected_selection_version": refreshed["selection"]["version"] + 1,
            },
        )
        assert stale_request.status_code == 409, stale_request.text
        assert stale_request.json() == {"code": "promotion_stale"}

        doomed = client.post(
            f"/api/v1/projects/{project['id']}/promotions",
            headers=headers,
            json={
                "client_mutation_id": str(uuid4()),
                "target_branch_id": target["id"],
                "expected_target_branch_head_revision_id": target["head"][
                    "graph_revision_id"
                ],
                "expected_target_branch_head_version": target["head"]["version"],
                "expected_selection_version": refreshed["selection"]["version"],
            },
        ).json()
        assert client.post(
            f"/api/v1/projects/{project['id']}/promotions/{doomed['id']}/approval-decisions",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": doomed["promotion_sha256"],
            },
        ).status_code == 200
        intervening = app.state.graph_changeset_service.request(
            project_id=project["id"],
            request=CreateGraphChangeSetRequest.model_validate(
                {
                    "base_graph_revision_id": target["head"]["graph_revision_id"],
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
                        "branch_id": target["id"],
                        "expected_branch_head_revision_id": target["head"][
                            "graph_revision_id"
                        ],
                        "expected_branch_head_version": target["head"]["version"],
                        "expected_selection_version": refreshed["selection"]["version"],
                    },
                }
            ),
            actor_id="local-session",
        )
        app.state.graph_changeset_service.approve(
            project_id=project["id"],
            change_set_id=intervening.id,
            expected_changeset_sha256=intervening.changeset_sha256,
            actor_id="local-session",
        )
        app.state.graph_changeset_service.apply(
            project_id=project["id"],
            change_set_id=intervening.id,
            expected_changeset_sha256=intervening.changeset_sha256,
            actor_id="local-session",
        )
        stale_apply = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{doomed['id']}/apply",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": doomed["promotion_sha256"],
            },
        )
        assert stale_apply.status_code == 409, stale_apply.text
        assert stale_apply.json() == {"code": "promotion_stale"}
        with app.state.graph_service.session_factory() as session:
            assert session.get(GraphPromotionRow, doomed["id"]).state == "STALE"

        refreshed = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        target = next(
            branch
            for branch in refreshed["branches"]
            if branch["id"] == str(experiment.created_branch_id)
        )
        with app.state.graph_service.session_factory() as session:
            before_count = session.scalar(select(func.count()).select_from(GraphRevisionRow))

        requested = client.post(
            f"/api/v1/projects/{project['id']}/promotions",
            headers=headers,
            json={
                "client_mutation_id": str(uuid4()),
                "target_branch_id": target["id"],
                "expected_target_branch_head_revision_id": target["head"][
                    "graph_revision_id"
                ],
                "expected_target_branch_head_version": target["head"]["version"],
                "expected_selection_version": refreshed["selection"]["version"],
            },
        )
        assert requested.status_code == 201, requested.text
        promotion = requested.json()
        approved = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{promotion['id']}/approval-decisions",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": promotion["promotion_sha256"],
            },
        )
        assert approved.status_code == 200, approved.text
        applied = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{promotion['id']}/apply",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": promotion["promotion_sha256"],
            },
        )
        assert applied.status_code == 200, applied.text
        promoted_workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        selected = promoted_workspace["selection"]
        assert selected["branch_id"] == target["id"]
        assert selected["graph_revision_id"] == target["head"]["graph_revision_id"]
        assert promoted_workspace["latest_promotion"]["id"] == promotion["id"]
        assert promoted_workspace["can_undo_latest_promotion"] is True
        assert client.get(
            f"/api/v1/projects/{project['id']}", headers=headers
        ).json()["active_revision_id"] == target["head"]["graph_revision_id"]
        with app.state.graph_service.session_factory() as session:
            after_count = session.scalar(select(func.count()).select_from(GraphRevisionRow))
        assert after_count == before_count

        undo = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{promotion['id']}/undo-proposals",
            headers=headers,
            json={"client_mutation_id": str(uuid4())},
        )
        assert undo.status_code == 201, undo.text
        reverse = undo.json()
        assert reverse["kind"] == "UNDO_PROMOTION"
        assert reverse["undo_of_promotion_id"] == promotion["id"]
        approved_reverse = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{reverse['id']}/approval-decisions",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": reverse["promotion_sha256"],
            },
        )
        assert approved_reverse.status_code == 200, approved_reverse.text
        applied_reverse = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{reverse['id']}/apply",
            headers=headers,
            json={
                "decision": "APPROVED",
                "expected_promotion_sha256": reverse["promotion_sha256"],
            },
        )
        assert applied_reverse.status_code == 200, applied_reverse.text
        undone_workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()
        restored = undone_workspace["selection"]
        assert restored["branch_id"] == main["id"]
        assert restored["graph_revision_id"] == main["head"]["graph_revision_id"]
        assert undone_workspace["latest_promotion"]["id"] == reverse["id"]
        assert undone_workspace["can_undo_latest_promotion"] is True
        stale_undo = client.post(
            f"/api/v1/projects/{project['id']}/promotions/{promotion['id']}/undo-proposals",
            headers=headers,
            json={"client_mutation_id": str(uuid4())},
        )
        assert stale_undo.status_code == 409, stale_undo.text
        assert stale_undo.json() == {"code": "promotion_undo_stale"}
        with app.state.graph_service.session_factory() as session:
            final_count = session.scalar(select(func.count()).select_from(GraphRevisionRow))
        assert final_count == before_count

    restarted = create_app(settings)
    with TestClient(restarted, base_url="http://testserver") as client:
        headers = _headers(restarted, client)
        restored_workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        )
        assert restored_workspace.status_code == 200, restored_workspace.text
        assert restored_workspace.json()["latest_promotion"]["id"] == reverse["id"]
        assert restored_workspace.json()["selection"] == restored
