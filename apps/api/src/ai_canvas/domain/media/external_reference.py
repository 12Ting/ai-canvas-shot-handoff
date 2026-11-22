from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


class ExternalRootViolation(ValueError):
    pass


class ExternalRootUnavailable(FileNotFoundError):
    pass


@dataclass(frozen=True, slots=True)
class ExternalRootGrant:
    id: UUID
    subject: str
    display_name: str
    canonical_path: Path
    volume_identity: str
    root_file_identity: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalFileObservation:
    canonical_path: Path
    relative_path: str
    file_identity: str
    sha256: str
    byte_size: int
    modified_at: datetime


def create_external_root_grant(
    root: Path,
    *,
    subject: str,
    display_name: str | None = None,
    now: datetime | None = None,
) -> ExternalRootGrant:
    if not subject:
        raise ValueError("external root grant subject is required")
    root_path = Path(root)
    _require_directory_without_reparse(root_path)
    try:
        canonical = root_path.resolve(strict=True)
        metadata = canonical.stat()
    except OSError as error:
        raise ExternalRootUnavailable("external root is unavailable") from error
    return ExternalRootGrant(
        id=uuid4(),
        subject=subject,
        display_name=display_name or canonical.name or str(canonical),
        canonical_path=canonical,
        volume_identity=str(metadata.st_dev),
        root_file_identity=_file_identity(metadata),
        created_at=now or datetime.now(timezone.utc),
    )


def resolve_granted_file(grant: ExternalRootGrant, relative_path: str) -> Path:
    relative = _validated_relative_path(relative_path)
    _require_directory_without_reparse(grant.canonical_path)
    candidate = grant.canonical_path.joinpath(*relative.parts)
    _require_path_chain_without_reparse(grant.canonical_path, candidate)
    try:
        canonical = candidate.resolve(strict=True)
    except OSError as error:
        raise ExternalRootUnavailable("external file is unavailable") from error
    try:
        canonical.relative_to(grant.canonical_path)
    except ValueError as error:
        raise ExternalRootViolation("external file escapes its granted root") from error
    try:
        metadata = canonical.lstat()
    except OSError as error:
        raise ExternalRootUnavailable("external file is unavailable") from error
    if not _is_regular_file(metadata) or _is_reparse(metadata):
        raise ExternalRootViolation("external file must be a regular non-reparse file")
    return canonical


def observe_granted_file(
    grant: ExternalRootGrant,
    relative_path: str,
    *,
    chunk_bytes: int = 1024 * 1024,
) -> ExternalFileObservation:
    if chunk_bytes < 1:
        raise ValueError("hash chunk size must be positive")
    canonical = resolve_granted_file(grant, relative_path)
    try:
        metadata = canonical.stat()
    except OSError as error:
        raise ExternalRootUnavailable("external file is unavailable") from error
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with canonical.open("rb") as source:
            while chunk := source.read(chunk_bytes):
                digest.update(chunk)
                byte_size += len(chunk)
    except OSError as error:
        raise ExternalRootUnavailable("external file is unavailable") from error
    if byte_size != metadata.st_size:
        raise ExternalRootViolation("external file changed while it was being hashed")
    return ExternalFileObservation(
        canonical_path=canonical,
        relative_path=_validated_relative_path(relative_path).as_posix(),
        file_identity=_file_identity(metadata),
        sha256=digest.hexdigest(),
        byte_size=byte_size,
        modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
    )


def _validated_relative_path(value: str) -> Path:
    if not value or "\x00" in value:
        raise ExternalRootViolation("external relative path is required")
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ExternalRootViolation("external path must be a non-empty relative path")
    return relative


def _require_path_chain_without_reparse(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise ExternalRootViolation("external file escapes its granted root") from error
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ExternalRootUnavailable("external file is unavailable") from error
        if _is_reparse(metadata):
            raise ExternalRootViolation("external path contains a reparse point")


def _require_directory_without_reparse(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExternalRootUnavailable("external root is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
        raise ExternalRootViolation("external root must be a non-reparse directory")


def _file_identity(metadata: os.stat_result) -> str:
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _is_regular_file(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode)


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )
