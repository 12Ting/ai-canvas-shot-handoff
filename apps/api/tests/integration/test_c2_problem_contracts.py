from pathlib import Path
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai_canvas.application.errors import (
    GraphBranchNameConflict,
    GraphChangeSetUndoStale,
    GraphChangeSetUndoUnsupported,
    PromotionStale,
    PromotionUndoStale,
    StaleImpactConfirmationRequired,
)
from ai_canvas.config import Settings
from ai_canvas.main import create_app


def test_c2_openapi_declares_problem_contracts_on_write_routes(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    contract = app.openapi()
    schemas = contract["components"]["schemas"]
    expected_schemas = {
        "BranchHeadStaleProblem",
        "SelectionPointerStaleProblem",
        "GraphBranchNameConflictProblem",
        "StaleImpactConfirmationRequiredProblem",
        "PromotionStaleProblem",
        "PromotionUndoStaleProblem",
        "GraphChangeSetUndoStaleProblem",
        "GraphChangeSetUndoUnsupportedProblem",
    }
    assert expected_schemas <= schemas.keys()

    cases = {
        (
            "/api/v1/projects/{project_id}/graph-change-sets",
            "post",
            "409",
        ): {"BranchHeadStaleProblem", "SelectionPointerStaleProblem"},
        (
            "/api/v1/projects/{project_id}/graph-change-sets/{change_set_id}/approval-decisions",
            "post",
            "422",
        ): {"StaleImpactConfirmationRequiredProblem"},
        (
            "/api/v1/projects/{project_id}/graph-change-sets/{change_set_id}/undo-proposals",
            "post",
            "409",
        ): {"GraphChangeSetUndoStaleProblem", "GraphChangeSetUndoUnsupportedProblem"},
        (
            "/api/v1/projects/{project_id}/promotions/{promotion_id}/apply",
            "post",
            "409",
        ): {"PromotionStaleProblem", "SelectionPointerStaleProblem"},
        (
            "/api/v1/projects/{project_id}/promotions/{promotion_id}/undo-proposals",
            "post",
            "409",
        ): {"PromotionUndoStaleProblem"},
    }
    for (path, method, status_code), expected in cases.items():
        rendered = json.dumps(
            contract["paths"][path][method]["responses"][status_code],
            sort_keys=True,
        )
        assert all(name in rendered for name in expected), (path, status_code, rendered)


def _headers(app, client: TestClient) -> dict[str, str]:
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert exchanged.status_code == 200
    return {
        "Authorization": f"Bearer {exchanged.json()['access_token']}",
        "Origin": "http://testserver",
    }


class _GraphChangeSetFailure:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def request(self, *_args, **_kwargs):
        raise self.error

    def approve(self, *_args, **_kwargs):
        raise self.error

    def undo_proposal(self, *_args, **_kwargs):
        raise self.error


class _PromotionFailure:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def request(self, *_args, **_kwargs):
        raise self.error

    def undo_proposal(self, *_args, **_kwargs):
        raise self.error


@pytest.mark.parametrize(
    ("error", "endpoint", "body", "expected_status", "expected_body"),
    [
        (
            GraphBranchNameConflict("main"),
            "graph-change-sets",
            {
                "base_graph_revision_id": "11111111-1111-4111-8111-111111111111",
                "client_mutation_id": "22222222-2222-4222-8222-222222222222",
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
            },
            409,
            {"code": "graph_branch_name_conflict", "normalized_name": "main"},
        ),
        (
            StaleImpactConfirmationRequired("a" * 64),
            "graph-change-sets/33333333-3333-4333-8333-333333333333/approval-decisions",
            {
                "decision": "APPROVED",
                "expected_changeset_sha256": "b" * 64,
            },
            422,
            {
                "code": "stale_impact_confirmation_required",
                "impact_sha256": "a" * 64,
            },
        ),
        (
            GraphChangeSetUndoStale(),
            "graph-change-sets/33333333-3333-4333-8333-333333333333/undo-proposals",
            {"client_mutation_id": "44444444-4444-4444-8444-444444444444"},
            409,
            {"code": "graph_changeset_undo_stale"},
        ),
        (
            GraphChangeSetUndoUnsupported(),
            "graph-change-sets/33333333-3333-4333-8333-333333333333/undo-proposals",
            {"client_mutation_id": "44444444-4444-4444-8444-444444444444"},
            409,
            {"code": "graph_changeset_undo_unsupported"},
        ),
    ],
)
def test_graph_changeset_c2_failures_have_stable_problem_contracts(
    tmp_path: Path,
    error: Exception,
    endpoint: str,
    body: dict,
    expected_status: int,
    expected_body: dict,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app, base_url="http://testserver", raise_server_exceptions=False
    ) as client:
        headers = _headers(app, client)
        app.state.graph_changeset_service = _GraphChangeSetFailure(error)
        response = client.post(
            f"/api/v1/projects/{uuid4()}/{endpoint}", headers=headers, json=body
        )

    assert response.status_code == expected_status, response.text
    assert response.json() == expected_body


@pytest.mark.parametrize(
    ("error", "suffix", "body", "expected_body"),
    [
        (
            PromotionStale(),
            "",
            {
                "client_mutation_id": "11111111-1111-4111-8111-111111111111",
                "target_branch_id": "22222222-2222-4222-8222-222222222222",
                "expected_target_branch_head_revision_id": "33333333-3333-4333-8333-333333333333",
                "expected_target_branch_head_version": 1,
                "expected_selection_version": 1,
            },
            {"code": "promotion_stale"},
        ),
        (
            PromotionUndoStale(),
            "/44444444-4444-4444-8444-444444444444/undo-proposals",
            {"client_mutation_id": "55555555-5555-4555-8555-555555555555"},
            {"code": "promotion_undo_stale"},
        ),
    ],
)
def test_promotion_c2_failures_have_stable_problem_contracts(
    tmp_path: Path,
    error: Exception,
    suffix: str,
    body: dict,
    expected_body: dict,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app, base_url="http://testserver", raise_server_exceptions=False
    ) as client:
        headers = _headers(app, client)
        app.state.promotion_service = _PromotionFailure(error)
        response = client.post(
            f"/api/v1/projects/{uuid4()}/promotions{suffix}",
            headers=headers,
            json=body,
        )

    assert response.status_code == 409, response.text
    assert response.json() == expected_body
