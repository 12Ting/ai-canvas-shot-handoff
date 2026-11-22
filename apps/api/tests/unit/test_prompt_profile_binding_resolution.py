from uuid import uuid4

from ai_canvas.contracts.graph import GraphSnapshotV3, ResourceBinding
from ai_canvas.domain.prompt_batches.profile_bindings import (
    resolve_effective_prompt_profile_binding,
)


def _binding(scope_kind, scope_id, revision_id, key="visual-profile"):
    return ResourceBinding(
        id=uuid4(),
        resource_kind="PROMPT",
        binding_key=key,
        scope_kind=scope_kind,
        scope_id=scope_id,
        resource_revision_id=revision_id,
    )


def test_node_overrides_shot_and_project_prompt_profile_binding() -> None:
    shot_id = uuid4()
    node_id = uuid4()
    project_revision = uuid4()
    shot_revision = uuid4()
    node_revision = uuid4()
    graph = GraphSnapshotV3(
        resource_bindings=[
            _binding("PROJECT", None, project_revision),
            _binding("SHOT", shot_id, shot_revision),
            _binding("NODE", node_id, node_revision),
        ]
    )

    resolved = resolve_effective_prompt_profile_binding(
        graph,
        binding_key="visual-profile",
        shot_id=shot_id,
        prompt_node_id=node_id,
    )

    assert resolved is not None
    assert resolved.resource_revision_id == node_revision
    assert resolved.scope_kind == "NODE"


def test_binding_resolution_falls_back_and_never_uses_other_keys_or_kinds() -> None:
    shot_id = uuid4()
    node_id = uuid4()
    project_revision = uuid4()
    graph = GraphSnapshotV3(
        resource_bindings=[
            _binding("PROJECT", None, project_revision),
            _binding("NODE", node_id, uuid4(), key="other"),
            ResourceBinding(
                id=uuid4(),
                resource_kind="STYLE",
                binding_key="visual-profile",
                scope_kind="NODE",
                scope_id=node_id,
                resource_revision_id=uuid4(),
            ),
        ]
    )

    resolved = resolve_effective_prompt_profile_binding(
        graph,
        binding_key="visual-profile",
        shot_id=shot_id,
        prompt_node_id=node_id,
    )

    assert resolved is not None
    assert resolved.resource_revision_id == project_revision
    assert resolved.scope_kind == "PROJECT"
