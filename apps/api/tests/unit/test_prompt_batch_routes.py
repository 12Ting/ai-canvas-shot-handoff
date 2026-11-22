from types import SimpleNamespace
from uuid import uuid4

from ai_canvas.api.routes.prompt_batches import finalize
from ai_canvas.contracts.prompt_batches import PromptBatchFinalizeRequest


def test_finalize_requires_graph_proposal_capability_and_delegates() -> None:
    project_id = uuid4()
    batch_id = uuid4()
    body = PromptBatchFinalizeRequest(
        expected_batch_version=8,
        client_mutation_id=uuid4(),
    )
    calls = []

    class PromptBatchService:
        def finalize(self, project_id_value, batch_id_value, body_value, *, actor_id):
            calls.append((project_id_value, batch_id_value, body_value, actor_id))
            return "finalized"

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(prompt_batch_service=PromptBatchService()),
        ),
    )

    assert finalize(project_id, batch_id, body, request) == "finalized"
    assert calls == [(project_id, batch_id, body, "local-session")]
