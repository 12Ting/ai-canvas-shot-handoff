from uuid import uuid4

import pytest

from ai_canvas.contracts.graph import (
    GraphSnapshotV1,
    GraphSnapshotV2,
    GraphSnapshotV3,
    Point,
    PromptNodeV2,
    ResourceBinding,
    Shot,
    ShotNodeV2,
)
from ai_canvas.domain.graph.snapshots import (
    canonical_snapshot,
    normalize_to_v3,
    parse_snapshot_json,
)
from ai_canvas.domain.graph.validation import GraphValidationError, validate_graph


def _v2_prompt_fixture(text: str) -> GraphSnapshotV2:
    return GraphSnapshotV2(
        nodes=[
            PromptNodeV2(
                id=uuid4(),
                kind="prompt",
                position=Point(x=1, y=2),
                data={"text": text},
            )
        ]
    )


def test_v1_and_v2_remain_parseable_after_v3_is_added() -> None:
    v1 = GraphSnapshotV1()
    v2 = GraphSnapshotV2()

    assert isinstance(parse_snapshot_json(v1.model_dump_json()), GraphSnapshotV1)
    assert isinstance(parse_snapshot_json(v2.model_dump_json()), GraphSnapshotV2)


def test_v2_prompt_normalizes_to_v3_without_text_drift() -> None:
    v3 = normalize_to_v3(_v2_prompt_fixture("原文🙂\n第二行"))

    prompt = next(node for node in v3.nodes if node.kind == "prompt")
    assert prompt.data.document.rendered_text == "原文🙂\n第二行"
    assert prompt.data.document.blocks[0].kind == "FREEFORM"
    assert prompt.data.document.blocks[0].locked is False


def test_v3_canonicalization_sorts_bindings_without_changing_document_blocks() -> None:
    base = normalize_to_v3(_v2_prompt_fixture("first\nsecond"))
    first = ResourceBinding(
        id=uuid4(),
        resource_kind="STYLE",
        binding_key="style.main",
        scope_kind="PROJECT",
        scope_id=None,
        resource_revision_id=uuid4(),
    )
    second = ResourceBinding(
        id=uuid4(),
        resource_kind="PROMPT",
        binding_key="prompt.main",
        scope_kind="PROJECT",
        scope_id=None,
        resource_revision_id=uuid4(),
    )
    left = base.model_copy(update={"resource_bindings": [first, second]})
    right = base.model_copy(update={"resource_bindings": [second, first]})

    assert canonical_snapshot(left) == canonical_snapshot(right)
    prompt = next(node for node in left.nodes if node.kind == "prompt")
    assert [block.text for block in prompt.data.document.blocks] == ["first\nsecond"]


def test_v3_rejects_duplicate_binding_keys_in_the_same_scope() -> None:
    binding = ResourceBinding(
        id=uuid4(),
        resource_kind="STYLE",
        binding_key="look",
        scope_kind="PROJECT",
        scope_id=None,
        resource_revision_id=uuid4(),
    )
    duplicate = binding.model_copy(
        update={"id": uuid4(), "resource_revision_id": uuid4()}
    )

    with pytest.raises(GraphValidationError, match="duplicate resource binding key"):
        validate_graph(GraphSnapshotV3(resource_bindings=[binding, duplicate]))


def test_v3_rejects_missing_shot_or_node_scope_targets() -> None:
    for scope_kind in ("SHOT", "NODE"):
        binding = ResourceBinding(
            id=uuid4(),
            resource_kind="STYLE",
            binding_key="look",
            scope_kind=scope_kind,
            scope_id=uuid4(),
            resource_revision_id=uuid4(),
        )
        with pytest.raises(GraphValidationError, match="scope target is missing"):
            validate_graph(GraphSnapshotV3(resource_bindings=[binding]))


def test_v3_accepts_a_binding_to_an_existing_shot_scope() -> None:
    shot_id = uuid4()
    shot_node = ShotNodeV2(
        id=uuid4(),
        kind="shot",
        position=Point(x=0, y=0),
        data={"shot_id": shot_id},
    )
    v3 = normalize_to_v3(
        GraphSnapshotV2(nodes=[shot_node], shots=[Shot(id=shot_id, order=0)])
    )
    binding = ResourceBinding(
        id=uuid4(),
        resource_kind="CHARACTER",
        binding_key="character.hero",
        scope_kind="SHOT",
        scope_id=shot_id,
        resource_revision_id=uuid4(),
    )
    graph = v3.model_copy(update={"resource_bindings": [binding]})

    assert validate_graph(graph) == graph
