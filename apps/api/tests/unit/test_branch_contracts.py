from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_canvas.contracts.branches import BranchApplicationTarget
from ai_canvas.domain.branching.names import normalize_branch_name


def test_branch_name_normalization_is_nfkc_trimmed_and_casefolded() -> None:
    assert normalize_branch_name("  Ｔｅｓｔ  ") == ("Ｔｅｓｔ", "test")


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_branch_name_rejects_empty_or_overlong_values(name: str) -> None:
    with pytest.raises(ValueError):
        normalize_branch_name(name)


def test_branch_application_target_is_strict_and_discriminated() -> None:
    branch_id = uuid4()
    revision_id = uuid4()
    adapter = TypeAdapter(BranchApplicationTarget)

    target = adapter.validate_python(
        {
            "mode": "DIRECT",
            "branch_id": branch_id,
            "expected_branch_head_revision_id": revision_id,
            "expected_branch_head_version": 3,
            "expected_selection_version": 4,
        }
    )
    assert target.mode == "DIRECT"
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "mode": "DIRECT",
                "branch_id": branch_id,
                "expected_branch_head_revision_id": revision_id,
                "expected_branch_head_version": 3,
                "expected_selection_version": 4,
                "branch_name": "not valid for direct",
            }
        )


def test_new_branch_target_requires_a_valid_name() -> None:
    adapter = TypeAdapter(BranchApplicationTarget)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "mode": "NEW_BRANCH",
                "base_branch_id": uuid4(),
                "branch_name": "",
                "expected_branch_head_revision_id": uuid4(),
                "expected_branch_head_version": 1,
                "expected_selection_version": 1,
            }
        )
