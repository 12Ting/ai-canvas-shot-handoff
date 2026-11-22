from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest


def test_external_reference_domain_module_exists() -> None:
    assert find_spec("ai_canvas.domain.media.external_reference") is not None


def test_granted_root_rejects_absolute_and_parent_escape(tmp_path: Path) -> None:
    module = import_module("ai_canvas.domain.media.external_reference")
    root = tmp_path / "root"
    root.mkdir()
    allowed = root / "clip.mp4"
    allowed.write_bytes(b"safe")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    grant = module.create_external_root_grant(root, subject="local")

    assert module.resolve_granted_file(grant, "clip.mp4") == allowed.resolve()
    for unsafe in ("../outside.mp4", str(outside), "", "."):
        with pytest.raises(module.ExternalRootViolation):
            module.resolve_granted_file(grant, unsafe)


def test_granted_root_rejects_reparse_escape_when_supported(tmp_path: Path) -> None:
    module = import_module("ai_canvas.domain.media.external_reference")
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"secret")
    link = root / "junction"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this test host does not permit creating a directory symlink")
    grant = module.create_external_root_grant(root, subject="local")

    with pytest.raises(module.ExternalRootViolation):
        module.resolve_granted_file(grant, "junction/secret.mp4")
