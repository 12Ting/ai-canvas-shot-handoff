import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_canvas.api.errors import register_error_handlers
from ai_canvas.application.errors import ClientMutationReuse
from ai_canvas.application.prompt_batch_service import (
    PromptBatchIntegrityError,
    PromptBatchStaleSubject,
    PromptBatchStateConflict,
)
from ai_canvas.application.run_control_service import EgressContentCategoryPolicyError


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_body"),
    [
        (
            PromptBatchStateConflict("secret detail"),
            409,
            {"code": "prompt_batch_state_conflict"},
        ),
        (
            PromptBatchStaleSubject("secret detail"),
            409,
            {"code": "prompt_batch_stale_subject"},
        ),
        (
            PromptBatchIntegrityError("secret detail"),
            409,
            {"code": "prompt_batch_integrity_error"},
        ),
        (
            EgressContentCategoryPolicyError("secret detail"),
            422,
            {"code": "egress_content_category_not_allowed"},
        ),
        (
            ClientMutationReuse("11111111-1111-4111-8111-111111111111"),
            409,
            {
                "code": "client_mutation_reuse",
                "client_mutation_id": "11111111-1111-4111-8111-111111111111",
            },
        ),
    ],
)
def test_c4_errors_have_stable_redacted_problem_bodies(
    error: Exception, expected_status: int, expected_body: dict[str, object]
) -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/probe")
    def probe() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert "secret detail" not in response.text
