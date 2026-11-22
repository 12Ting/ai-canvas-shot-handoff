from __future__ import annotations

import asyncio
import errno
import gc
import hashlib
import os
import subprocess
import threading
import unicodedata
import weakref
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_canvas.contracts.artifact import (
    ArtifactImportConflictReason,
    ArtifactImportFailureCode,
)
from ai_canvas.infrastructure.artifacts import store as store_module
from ai_canvas.infrastructure.artifacts.store import (
    ArtifactStagingConflictError,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactStorageIOError,
    EmptyArtifactError,
    PreparedPromotionPartialError,
    StagedArtifact,
    StoredBlobIntegrityError,
    UnsafeStagingPathError,
    UploadDeadline,
    UploadDeadlineExceeded,
    UploadSizeError,
    next_chunk_with_deadline,
    sanitize_filename,
)


def _promotion_candidates(data_dir: Path) -> list[Path]:
    root = data_dir / "artifacts" / ".promotion"
    return sorted(root.iterdir()) if root.exists() else []


@pytest.mark.parametrize("attack", ["replace", "overwrite_restore_mtime"])
def test_verified_promotion_never_publishes_public_staging_changed_after_identity_check(
    tmp_path: Path,
    monkeypatch,
    attack: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-private-bytes"
    attacker_content = b"unverified-evil-bytes!"
    assert len(attacker_content) == len(content)
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    original_atomic_promote = store_module._atomic_promote

    def attack_public_path_before_publish(source: Path, destination: Path) -> None:
        if attack == "replace":
            replacement = staged.path.with_suffix(".attacker")
            replacement.write_bytes(attacker_content)
            os.replace(replacement, staged.path)
        else:
            if not staged.path.exists():
                staged.path.write_bytes(content)
            metadata = staged.path.stat()
            staged.path.write_bytes(attacker_content)
            os.utime(
                staged.path,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
            )
        original_atomic_promote(source, destination)

    monkeypatch.setattr(store_module, "_atomic_promote", attack_public_path_before_publish)

    stored = store.promote_verified(ticket)

    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert stored.path.read_bytes() == content
    assert hashlib.sha256(stored.path.read_bytes()).hexdigest() == stored.sha256
    assert staged.path.read_bytes() == attacker_content


def test_revoke_promotion_cleans_only_private_candidate_after_public_path_is_rebuilt(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-then-precondition-failed"
    replacement = b"new-public-upload-must-survive"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))

    ticket = store.prepare_promotion(staged)
    candidates = _promotion_candidates(tmp_path)
    assert not staged.path.exists()
    assert len(candidates) == 1
    assert candidates[0].read_bytes() == content
    staged.path.write_bytes(replacement)

    assert store.revoke_promotion(ticket) is True
    assert store.revoke_promotion(ticket) is False
    assert staged.path.read_bytes() == replacement
    assert _promotion_candidates(tmp_path) == []
    assert not store.blob_path(staged.sha256).exists()


def test_abandoned_promotion_ticket_gc_removes_private_candidate(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"abandoned-private-candidate"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    observed_ticket = weakref.ref(ticket)

    assert not staged.path.exists()
    assert len(_promotion_candidates(tmp_path)) == 1
    del ticket
    gc.collect()

    assert observed_ticket() is None
    assert _promotion_candidates(tmp_path) == []


def test_concurrent_verified_promotion_consumes_ticket_once_without_candidate_leak(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"concurrent-private-candidate"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    start = threading.Barrier(3)
    stored_results = []
    errors: list[BaseException] = []

    def consume() -> None:
        start.wait()
        try:
            stored_results.append(store.promote_verified(ticket))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(stored_results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ArtifactStoreError)
    assert stored_results[0].path.read_bytes() == content
    assert _promotion_candidates(tmp_path) == []


@pytest.mark.parametrize("damage", ["modified", "replaced", "deleted"])
def test_verified_promotion_rejects_existing_cas_identity_changes(
    tmp_path: Path,
    damage: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-existing"
    first = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.promote(first).path
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    if damage == "modified":
        destination.write_bytes(b"X" * len(content))
    elif damage == "replaced":
        replacement = destination.with_suffix(".replacement")
        replacement.write_bytes(content)
        os.replace(replacement, destination)
    else:
        destination.unlink()

    with pytest.raises(ArtifactStoreError) as captured:
        store.promote_verified(ticket)

    assert captured.value.failure_code is ArtifactImportFailureCode.UPLOAD_INTERRUPTED
    assert not staged.path.exists()
    assert _promotion_candidates(tmp_path) == []


def test_verified_promotion_ticket_is_one_time_store_bound_and_unforgeable(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    other_store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"opaque-capability"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    forged = object.__new__(type(ticket))

    with pytest.raises(ArtifactStoreError):
        other_store.promote_verified(ticket)
    stored = store.promote_verified(ticket)
    assert stored.path.read_bytes() == content
    with pytest.raises(ArtifactStoreError):
        store.promote_verified(forged)

    retry_staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    retry_ticket = store.prepare_promotion(retry_staged)
    retry_stored = store.promote_verified(retry_ticket)
    assert retry_stored.path.read_bytes() == content
    with pytest.raises(ArtifactStoreError):
        store.promote_verified(retry_ticket)


def test_promote_verified_never_rehashes_after_prepare(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"hash-outside-gate"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)

    def forbidden_rehash(*_args, **_kwargs):
        raise AssertionError("promote_verified must not perform full-file verification")

    monkeypatch.setattr(store_module, "_file_matches", forbidden_rehash)

    stored = store.promote_verified(ticket)

    assert stored.path.read_bytes() == content


def test_sealed_ticket_publishes_while_same_digest_prepare_is_hashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"same-digest-hash-does-not-convoy-publish"
    blocked_staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    publisher_staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    original_file_matches = store_module._file_matches
    hashing_started = threading.Event()
    release_hash = threading.Event()
    publisher_done = threading.Event()
    blocked_tickets = []
    publisher_results = []
    errors: list[BaseException] = []

    def block_one_candidate_hash(path: Path, **kwargs) -> bool:
        if path.parent == store.promotion_root and not hashing_started.is_set():
            hashing_started.set()
            if not release_hash.wait(5):
                raise AssertionError("blocked hash was never released")
        return original_file_matches(path, **kwargs)

    monkeypatch.setattr(store_module, "_file_matches", block_one_candidate_hash)

    def prepare_blocked() -> None:
        try:
            blocked_tickets.append(store.prepare_promotion(blocked_staged))
        except BaseException as error:
            errors.append(error)

    def publish_sealed_ticket() -> None:
        try:
            ticket = store.prepare_promotion(publisher_staged)
            publisher_results.append(store.promote_verified(ticket))
        except BaseException as error:
            errors.append(error)
        finally:
            publisher_done.set()

    blocked = threading.Thread(target=prepare_blocked, name="blocked-same-digest-prepare")
    publisher = threading.Thread(target=publish_sealed_ticket, name="sealed-ticket-publisher")
    blocked.start()
    assert hashing_started.wait(5)
    publisher.start()
    try:
        assert publisher_done.wait(0.75) is True
    finally:
        release_hash.set()
        blocked.join(timeout=5)
        publisher.join(timeout=5)

    assert not blocked.is_alive()
    assert not publisher.is_alive()
    assert not errors
    assert len(publisher_results) == 1
    assert publisher_results[0].path.read_bytes() == content
    assert len(blocked_tickets) == 1
    assert store.revoke_promotion(blocked_tickets[0]) is True
    assert _promotion_candidates(tmp_path) == []


def test_reconcile_promotion_candidates_removes_fresh_private_candidate(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    store._prepare_promotion_root()
    candidate = store.promotion_root / f"{uuid4().hex}.candidate"
    candidate.write_bytes(b"fresh-crash-private-candidate")

    assert store.reconcile_promotion_candidates() == (candidate,)
    assert not candidate.exists()


def test_reconcile_promotion_candidates_converges_partial_cas_hard_link(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"partial-cas-link-recovery"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    candidate = _promotion_candidates(tmp_path)[0]
    destination = store.blob_path(staged.sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(candidate, destination)
    except OSError as error:
        pytest.skip(f"hard-link setup is unavailable: {error}")

    assert store.reconcile_promotion_candidates() == (candidate,)
    assert not candidate.exists()
    assert destination.read_bytes() == content
    assert store.revoke_promotion(ticket) is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory durability semantics")
def test_posix_publish_sync_failure_converges_private_and_cas_hard_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"posix-publish-fsync-after-link"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    destination = store.blob_path(staged.sha256)
    original_sync = store_module._fsync_directory_strict
    failed = False

    def fail_only_first_destination_sync(directory: Path) -> None:
        nonlocal failed
        if directory == destination.parent and not failed:
            failed = True
            raise OSError(errno.EIO, "injected publish directory sync failure")
        original_sync(directory)

    monkeypatch.setattr(store_module, "_fsync_directory_strict", fail_only_first_destination_sync)

    with pytest.raises(ArtifactStorageIOError):
        store.promote_verified(ticket)

    assert failed is True
    assert destination.read_bytes() == content
    assert _promotion_candidates(tmp_path) == []


def test_prepare_promotion_rejects_identity_change_during_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"identity-changed-during-hash"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    original_file_matches = store_module._file_matches

    def replace_after_hash(path: Path, **kwargs) -> bool:
        matches = original_file_matches(path, **kwargs)
        if path.parent == store.promotion_root:
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(content)
            os.replace(replacement, path)
        return matches

    monkeypatch.setattr(store_module, "_file_matches", replace_after_hash)

    with pytest.raises(ArtifactStoreError) as captured:
        store.prepare_promotion(staged)

    assert captured.value.failure_code is ArtifactImportFailureCode.UPLOAD_INTERRUPTED
    assert not staged.path.exists()
    assert len(_promotion_candidates(tmp_path)) == 1
    assert not store.blob_path(staged.sha256).exists()


def test_prepare_promotion_removes_unchanged_candidate_after_hash_validation_failure(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-before-public-tamper"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    staged.path.write_bytes(b"X" * len(content))

    with pytest.raises(ArtifactStoreError):
        store.prepare_promotion(staged)

    assert not staged.path.exists()
    assert _promotion_candidates(tmp_path) == []
    assert not store.blob_path(staged.sha256).exists()


def test_prepare_promotion_removes_candidate_after_existing_cas_validation_failure(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-candidate-corrupt-existing-cas"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"corrupt-existing-cas")

    with pytest.raises(ArtifactStoreError):
        store.prepare_promotion(staged)

    assert not staged.path.exists()
    assert _promotion_candidates(tmp_path) == []
    assert destination.read_bytes() == b"corrupt-existing-cas"


@pytest.mark.parametrize("failed_root", ["promotion", "staging"])
def test_private_takeover_sync_failure_has_no_ambiguous_owner_and_can_retry(
    tmp_path: Path,
    monkeypatch,
    failed_root: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"atomic-private-takeover"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    target = store.promotion_root if failed_root == "promotion" else store.staging_root
    injected = False

    def fail_one_takeover_sync(directory: Path) -> None:
        nonlocal injected
        if directory == target and not injected:
            injected = True
            raise OSError(errno.EIO, "injected takeover sync failure")

    monkeypatch.setattr(
        store_module,
        "_sync_takeover_directory",
        fail_one_takeover_sync,
        raising=False,
    )

    with pytest.raises(ArtifactStoreError):
        store.prepare_promotion(staged)

    assert injected is True
    assert staged.path.read_bytes() == content
    assert _promotion_candidates(tmp_path) == []
    restarted = ArtifactStore(tmp_path, max_bytes=64)
    stored = restarted.promote_verified(restarted.prepare_promotion(staged))
    assert stored.path.read_bytes() == content


def test_private_takeover_failure_before_rename_preserves_public_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"takeover-never-started"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))

    def fail_before_rename(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EIO, "injected pre-rename failure")

    monkeypatch.setattr(
        store_module,
        "_atomic_takeover",
        fail_before_rename,
        raising=False,
    )

    with pytest.raises(ArtifactStoreError):
        store.prepare_promotion(staged)

    assert staged.path.read_bytes() == content
    assert _promotion_candidates(tmp_path) == []


@pytest.mark.parametrize("move_before_error", [False, True])
def test_posix_takeover_fault_preserves_the_only_owned_data_location(
    tmp_path: Path,
    monkeypatch,
    move_before_error: bool,
) -> None:
    source = tmp_path / "public.part"
    destination = tmp_path / "private.candidate"
    content = b"single-owned-file-after-rename-fault"
    source.write_bytes(content)
    original_replace = store_module.os.replace

    def fail_replace(current_source: Path, current_destination: Path) -> None:
        if move_before_error:
            original_replace(current_source, current_destination)
        raise OSError(errno.EIO, "injected rename fault")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    expected_error = PreparedPromotionPartialError if move_before_error else OSError

    with pytest.raises(expected_error):
        store_module._posix_atomic_takeover(source, destination)

    if move_before_error:
        assert not source.exists()
        assert destination.read_bytes() == content
    else:
        assert source.read_bytes() == content
        assert not destination.exists()


def test_private_takeover_rollback_failure_retains_exact_recoverable_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"takeover-rollback-needs-recovery"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    original_takeover = store_module._atomic_takeover
    takeover_calls = 0
    sync_calls = 0

    def fail_rollback(source: Path, destination: Path) -> None:
        nonlocal takeover_calls
        takeover_calls += 1
        if takeover_calls == 2:
            raise OSError(errno.EIO, "injected rollback failure")
        original_takeover(source, destination)

    def fail_first_sync(_directory: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise OSError(errno.EIO, "injected post-rename sync failure")

    monkeypatch.setattr(store_module, "_atomic_takeover", fail_rollback)
    monkeypatch.setattr(store_module, "_sync_takeover_directory", fail_first_sync)

    with pytest.raises(PreparedPromotionPartialError):
        store.prepare_promotion(staged)

    candidates = _promotion_candidates(tmp_path)
    assert takeover_calls == 2
    assert not staged.path.exists()
    assert len(candidates) == 1
    assert candidates[0].read_bytes() == content
    assert not store.blob_path(staged.sha256).exists()


def test_private_takeover_retries_random_candidate_collision_without_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"candidate-collision-retry"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    store._prepare_promotion_root()
    collision_id = UUID("00000000-0000-0000-0000-000000000001")
    retry_id = UUID("00000000-0000-0000-0000-000000000002")
    collision = store.promotion_root / f"{collision_id.hex}.candidate"
    collision.write_bytes(b"preexisting-private-candidate")
    candidate_ids = iter((collision_id, retry_id))
    monkeypatch.setattr(store_module, "uuid4", lambda: next(candidate_ids))

    ticket = store.prepare_promotion(staged)
    stored = store.promote_verified(ticket)

    assert collision.read_bytes() == b"preexisting-private-candidate"
    assert stored.path.read_bytes() == content


def test_revoke_changed_candidate_fails_closed_without_deleting_replacement(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-candidate"
    replacement_content = b"changed-private-candidate"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    candidate = _promotion_candidates(tmp_path)[0]
    replacement = candidate.with_suffix(".replacement")
    replacement.write_bytes(replacement_content)
    os.replace(replacement, candidate)

    with pytest.raises(ArtifactStoreError):
        store.revoke_promotion(ticket)

    assert candidate.read_bytes() == replacement_content
    assert not staged.path.exists()
    assert not store.blob_path(staged.sha256).exists()


def test_abandoned_changed_candidate_is_not_deleted_by_ticket_finalizer(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-candidate"
    replacement_content = b"changed-private-candidate"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    candidate = _promotion_candidates(tmp_path)[0]
    replacement = candidate.with_suffix(".replacement")
    replacement.write_bytes(replacement_content)
    os.replace(replacement, candidate)
    observed_ticket = weakref.ref(ticket)

    del ticket
    gc.collect()

    assert observed_ticket() is None
    assert candidate.read_bytes() == replacement_content


def test_promote_verified_rejects_cas_that_appeared_after_prepare_and_consumes_ticket(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"late-valid-cas"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    ticket = store.prepare_promotion(staged)
    destination = store.blob_path(staged.sha256)
    destination.write_bytes(content)

    with pytest.raises(ArtifactStoreError):
        store.promote_verified(ticket)
    with pytest.raises(ArtifactStoreError):
        store.promote_verified(ticket)

    retry_staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    retry = store.prepare_promotion(retry_staged)
    stored = store.promote_verified(retry)
    assert stored.reused is True
    assert destination.read_bytes() == content
    assert not retry_staged.path.exists()


def test_post_publication_staging_cleanup_never_unlinks_reused_shared_cas(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"shared-cas-survives-db-failure-cleanup"
    first = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.promote(first).path
    retry_staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))

    reused = store.promote_verified(store.prepare_promotion(retry_staged))
    store.discard(retry_staged)

    assert reused.reused is True
    assert destination.read_bytes() == content
    assert not retry_staged.path.exists()


def _create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def _assert_storage_io_error(error: Exception, unsafe_path: Path) -> None:
    assert isinstance(error, ArtifactStorageIOError)
    assert error.failure_code is ArtifactImportFailureCode.UPLOAD_INTERRUPTED
    assert str(error) == "artifact storage is temporarily unavailable"
    assert str(unsafe_path) not in str(error)


def _assert_staging_conflict(error: Exception, unsafe_path: Path) -> None:
    assert isinstance(error, ArtifactStagingConflictError)
    assert error.reason is ArtifactImportConflictReason.UPLOAD_IN_PROGRESS
    assert not hasattr(error, "failure_code")
    assert str(error) == "an artifact upload is already in progress"
    assert str(unsafe_path) not in str(error)


def test_stage_chunks_streams_sha256_counts_bytes_and_fsyncs(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64, chunk_size=4)
    chunks = [b"stream", b"ing-", b"content"]
    fsynced: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        fsynced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", recording_fsync)
    staged = store.stage_chunks(uuid4(), iter(chunks), declared_size=17)

    assert staged.byte_size == 17
    assert staged.sha256 == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert staged.path.read_bytes() == b"".join(chunks)
    assert staged.path.parent == tmp_path / "artifacts" / ".staging"
    assert fsynced, "the staging file must be fsynced before it is returned"


def test_async_stage_streams_chunks_incrementally_and_returns_hash_and_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64, chunk_size=4)
    import_id = uuid4()
    staging_path = store.staging_path(import_id)
    content = [b"async-", b"stream", b"-body"]
    writes: list[bytes] = []
    original_write = store_module._StagingWriter.write

    def recording_write(writer, chunk: bytes) -> None:
        original_write(writer, chunk)
        writes.append(bytes(chunk))

    monkeypatch.setattr(store_module._StagingWriter, "write", recording_write)

    async def body():
        yield content[0]
        assert writes == content[:1]
        assert staging_path.exists()
        yield content[1]
        assert writes == content[:2]
        yield content[2]

    staged = asyncio.run(
        store.stage_async_chunks(
            import_id,
            body(),
            declared_size=sum(map(len, content)),
            deadline=UploadDeadline(),
        )
    )

    expected = b"".join(content)
    assert staged.byte_size == len(expected)
    assert staged.sha256 == hashlib.sha256(expected).hexdigest()
    assert staged.path.read_bytes() == expected


def test_async_stage_offloads_disk_writes_so_the_event_loop_keeps_progressing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = store_module._StagingWriter.write

    def blocking_write(writer, chunk: bytes) -> None:
        write_started.set()
        assert release_write.wait(timeout=1.0), "event loop did not advance during disk write"
        original_write(writer, chunk)

    monkeypatch.setattr(store_module._StagingWriter, "write", blocking_write)

    async def body():
        yield b"offloaded"

    async def scenario() -> None:
        task = asyncio.create_task(
            store.stage_async_chunks(
                uuid4(),
                body(),
                declared_size=9,
                deadline=UploadDeadline(),
            )
        )
        while not write_started.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        still_writing = not task.done()
        release_write.set()
        await task
        assert still_writing

    asyncio.run(scenario())


def test_async_stage_idle_deadline_is_typed_and_cleans_its_part(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = uuid4()

    async def stalled_body():
        await asyncio.sleep(0.05)
        yield b"late"

    with pytest.raises(UploadDeadlineExceeded) as captured:
        asyncio.run(
            store.stage_async_chunks(
                import_id,
                stalled_body(),
                declared_size=4,
                deadline=UploadDeadline(idle_timeout_seconds=0.01, total_timeout_seconds=1.0),
            )
        )

    assert captured.value.kind == "idle"
    assert not store.staging_path(import_id).exists()


def test_async_stage_checks_total_deadline_after_a_disk_write(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = uuid4()
    original_write = store_module._StagingWriter.write

    def slow_write(writer, chunk: bytes) -> None:
        threading.Event().wait(timeout=0.05)
        original_write(writer, chunk)

    monkeypatch.setattr(store_module._StagingWriter, "write", slow_write)

    async def body():
        yield b"slow"

    with pytest.raises(UploadDeadlineExceeded) as captured:
        asyncio.run(
            store.stage_async_chunks(
                import_id,
                body(),
                declared_size=4,
                deadline=UploadDeadline(idle_timeout_seconds=1.0, total_timeout_seconds=0.01),
            )
        )

    assert captured.value.kind == "total"
    assert not store.staging_path(import_id).exists()


def test_async_stage_cancellation_waits_for_worker_then_removes_its_part(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = uuid4()
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = store_module._StagingWriter.write

    def blocking_write(writer, chunk: bytes) -> None:
        write_started.set()
        assert release_write.wait(timeout=1.0)
        original_write(writer, chunk)

    monkeypatch.setattr(store_module._StagingWriter, "write", blocking_write)

    async def body():
        yield b"cancel-me"

    async def scenario() -> None:
        task = asyncio.create_task(
            store.stage_async_chunks(
                import_id,
                body(),
                declared_size=9,
                deadline=UploadDeadline(),
            )
        )
        while not write_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        cleanup_is_waiting = not task.done()
        release_write.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup_is_waiting

    asyncio.run(scenario())
    assert not store.staging_path(import_id).exists()


@pytest.mark.parametrize("phase", ["open", "write", "finish"])
def test_async_stage_repeated_cancellation_settles_worker_and_abort_before_returning(
    tmp_path: Path,
    monkeypatch,
    phase: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = uuid4()
    staging_path = store.staging_path(import_id)
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_settled = threading.Event()
    abort_started = threading.Event()
    release_abort = threading.Event()
    abort_settled = threading.Event()
    abort_saw_worker_settled: list[bool] = []
    original_open = store._open_staging_writer
    original_write = store_module._StagingWriter.write
    original_finish = store_module._StagingWriter.finish
    original_abort = store_module._StagingWriter.abort
    should_block = True

    def wait_at_phase() -> None:
        nonlocal should_block
        if not should_block:
            return
        should_block = False
        worker_started.set()
        assert release_worker.wait(timeout=1.0)

    def blocking_open(*args, **kwargs):
        writer = original_open(*args, **kwargs)
        if phase == "open":
            try:
                wait_at_phase()
            finally:
                worker_settled.set()
        return writer

    def blocking_write(writer, chunk: bytes) -> None:
        if phase != "write":
            original_write(writer, chunk)
            return
        try:
            wait_at_phase()
            original_write(writer, chunk)
        finally:
            worker_settled.set()

    def blocking_finish(writer):
        if phase != "finish":
            return original_finish(writer)
        try:
            wait_at_phase()
            return original_finish(writer)
        finally:
            worker_settled.set()

    def blocking_abort(writer) -> None:
        abort_saw_worker_settled.append(worker_settled.is_set())
        abort_started.set()
        assert release_abort.wait(timeout=1.0)
        try:
            original_abort(writer)
        finally:
            abort_settled.set()

    monkeypatch.setattr(store, "_open_staging_writer", blocking_open)
    monkeypatch.setattr(store_module._StagingWriter, "write", blocking_write)
    monkeypatch.setattr(store_module._StagingWriter, "finish", blocking_finish)
    monkeypatch.setattr(store_module._StagingWriter, "abort", blocking_abort)

    async def body():
        yield b"cancel-and-retry"

    async def scenario() -> tuple[bool, bool, bool]:
        task = asyncio.create_task(
            store.stage_async_chunks(
                import_id,
                body(),
                declared_size=16,
                deadline=UploadDeadline(),
            )
        )
        while not worker_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.01)
        returned_before_worker_settled = task.done()
        release_worker.set()
        assert await asyncio.to_thread(worker_settled.wait, 1.0)
        abort_was_started = await asyncio.to_thread(abort_started.wait, 0.2)
        if abort_was_started:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0.01)
        returned_before_abort_settled = task.done()
        release_abort.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        if abort_was_started:
            assert await asyncio.to_thread(abort_settled.wait, 1.0)
        return returned_before_worker_settled, abort_was_started, returned_before_abort_settled

    try:
        returned_early, abort_was_started, returned_before_abort = asyncio.run(scenario())
        assert returned_early is False
        assert abort_was_started is True
        assert abort_saw_worker_settled == [True]
        assert returned_before_abort is False
        assert worker_settled.is_set()
        assert abort_settled.is_set()
        assert not staging_path.exists()

        retry = store.stage_chunks(import_id, [b"retry"], declared_size=5)
        assert retry.path == staging_path
        store.discard(retry)
    finally:
        release_worker.set()
        release_abort.set()
        staging_path.unlink(missing_ok=True)


def test_async_stage_collision_never_truncates_the_existing_part(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = uuid4()
    existing = store.staging_path(import_id)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing-upload")

    async def body():
        yield b"replacement"

    with pytest.raises(Exception) as captured:
        asyncio.run(
            store.stage_async_chunks(
                import_id,
                body(),
                declared_size=11,
                deadline=UploadDeadline(),
            )
        )

    _assert_staging_conflict(captured.value, existing)
    assert existing.read_bytes() == b"existing-upload"


@pytest.mark.parametrize("operation", ["promote", "discard", "cleanup"])
def test_nonstandard_canonical_uuid_remains_manageable_across_store_lifecycle(
    tmp_path: Path,
    operation: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    import_id = UUID("12345678-1234-f678-0234-567812345678")
    content = b"canonical-non-rfc-version"
    staged = store.stage_chunks(import_id, [content], declared_size=len(content))

    if operation == "promote":
        stored = store.promote(staged)
        assert stored.path.read_bytes() == content
        assert not staged.path.exists()
    elif operation == "discard":
        store.discard(staged)
        assert not staged.path.exists()
    else:
        os.utime(staged.path, (1_000.0, 1_000.0))
        assert store.cleanup_staging(max_age_seconds=3_600.0, now=5_000.0) == [staged.path]
        assert not staged.path.exists()


def test_declared_size_over_limit_is_rejected_without_consuming_body(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=8)

    def body():
        raise AssertionError("an invalid declaration must be rejected before body consumption")
        yield b"unreachable"

    with pytest.raises(UploadSizeError) as captured:
        store.stage_chunks(uuid4(), body(), declared_size=9)

    assert captured.value.failure_code is ArtifactImportFailureCode.SIZE_MISMATCH
    assert not store.staging_root.exists()


def test_actual_overflow_removes_only_its_generated_staging_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=8, chunk_size=4)
    import_id = uuid4()
    store.staging_root.mkdir(parents=True)
    unrelated = store.staging_root / f"{uuid4()}.part"
    unrelated.write_bytes(b"keep")

    with pytest.raises(UploadSizeError):
        store.stage_chunks(import_id, [b"1234", b"5678", b"9"], declared_size=8)

    assert not store.staging_path(import_id).exists()
    assert unrelated.read_bytes() == b"keep"


def test_existing_staging_collision_is_never_deleted_or_truncated(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=16)
    import_id = uuid4()
    existing = store.staging_path(import_id)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"upload-already-in-progress")

    with pytest.raises(Exception) as captured:
        store.stage_chunks(import_id, [b"new"], declared_size=3)

    _assert_staging_conflict(captured.value, existing)
    assert existing.read_bytes() == b"upload-already-in-progress"


@pytest.mark.parametrize("failure_point", ["open", "write", "fsync"])
def test_sync_stage_translates_filesystem_failures_without_leaking_paths(
    tmp_path: Path,
    monkeypatch,
    failure_point: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=16)
    import_id = uuid4()
    staging_path = store.staging_path(import_id)
    injected = OSError(errno.EIO, "injected storage failure", str(staging_path))

    if failure_point == "open":
        real_open = Path.open

        def failing_open(path: Path, *args, **kwargs):
            if path == staging_path and args and args[0] == "xb":
                raise injected
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", failing_open)
    elif failure_point == "write":

        def failing_write(_writer, _chunk: bytes) -> None:
            raise injected

        monkeypatch.setattr(store_module._StagingWriter, "write", failing_write)
    else:
        def failing_fsync(_descriptor: int) -> None:
            raise injected

        monkeypatch.setattr(store_module.os, "fsync", failing_fsync)

    with pytest.raises(Exception) as captured:
        store.stage_chunks(import_id, [b"data"], declared_size=4)

    _assert_storage_io_error(captured.value, staging_path)
    assert not staging_path.exists()


def test_async_stage_translates_worker_filesystem_failure_and_cleans_part(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=16)
    import_id = uuid4()
    staging_path = store.staging_path(import_id)

    def failing_write(_writer, _chunk: bytes) -> None:
        raise OSError(errno.EIO, "async worker storage failure", str(staging_path))

    monkeypatch.setattr(store_module._StagingWriter, "write", failing_write)

    async def body():
        yield b"data"

    with pytest.raises(Exception) as captured:
        asyncio.run(
            store.stage_async_chunks(
                import_id,
                body(),
                declared_size=4,
                deadline=UploadDeadline(),
            )
        )

    _assert_storage_io_error(captured.value, staging_path)
    assert not staging_path.exists()


def test_artifacts_ancestor_reparse_blocks_stage_without_external_writes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    data_dir.mkdir()
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    artifacts_link = data_dir / "artifacts"
    _create_directory_link(artifacts_link, external)
    store = ArtifactStore(data_dir, max_bytes=16)

    try:
        assert store_module._is_symlink_or_reparse(artifacts_link)
        with pytest.raises(UnsafeStagingPathError):
            store.stage_chunks(uuid4(), [b"new"], declared_size=3)
        assert sentinel.read_bytes() == b"unchanged"
        assert not (external / ".staging").exists()
    finally:
        _remove_directory_link(artifacts_link)


def test_artifacts_ancestor_reparse_blocks_cleanup_without_external_deletes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    external_staging = external / ".staging"
    data_dir.mkdir()
    external_staging.mkdir(parents=True)
    external_part = external_staging / f"{uuid4()}.part"
    external_part.write_bytes(b"external")
    os.utime(external_part, (1_000.0, 1_000.0))
    artifacts_link = data_dir / "artifacts"
    _create_directory_link(artifacts_link, external)
    store = ArtifactStore(data_dir)

    try:
        with pytest.raises(UnsafeStagingPathError):
            store.cleanup_staging(max_age_seconds=3_600.0, now=5_000.0)
        assert external_part.read_bytes() == b"external"
    finally:
        _remove_directory_link(artifacts_link)


def test_artifacts_ancestor_reparse_blocks_promote_without_external_mutation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    external_staging = external / ".staging"
    data_dir.mkdir()
    external_staging.mkdir(parents=True)
    import_id = uuid4()
    content = b"external-staging"
    external_part = external_staging / f"{import_id}.part"
    external_part.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    artifacts_link = data_dir / "artifacts"
    _create_directory_link(artifacts_link, external)
    store = ArtifactStore(data_dir)
    staged = StagedArtifact(import_id, store.staging_path(import_id), digest, len(content))

    try:
        with pytest.raises(UnsafeStagingPathError):
            store.promote(staged)
        assert external_part.read_bytes() == content
        assert not (external / "blobs").exists()
    finally:
        _remove_directory_link(artifacts_link)


def test_promotion_root_reparse_blocks_candidate_takeover_without_external_mutation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"unchanged")
    store = ArtifactStore(data_dir, max_bytes=64)
    content = b"candidate-must-not-cross-reparse"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    _create_directory_link(store.promotion_root, external)

    try:
        with pytest.raises(UnsafeStagingPathError):
            store.prepare_promotion(staged)
        assert staged.path.read_bytes() == content
        assert sentinel.read_bytes() == b"unchanged"
        assert sorted(path.name for path in external.iterdir()) == ["sentinel"]
    finally:
        _remove_directory_link(store.promotion_root)


def test_empty_content_and_short_body_are_rejected_and_cleaned(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=16)
    empty_id = uuid4()
    short_id = uuid4()

    with pytest.raises(EmptyArtifactError) as empty:
        store.stage_chunks(empty_id, [b"", b""], declared_size=None)
    with pytest.raises(UploadSizeError):
        store.stage_chunks(short_id, [b"abc"], declared_size=4)

    assert empty.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA
    assert not store.staging_path(empty_id).exists()
    assert not store.staging_path(short_id).exists()


def test_upload_deadline_enforces_idle_and_total_limits() -> None:
    deadline = UploadDeadline(started_at=100.0)

    assert deadline.idle_timeout_seconds == 30.0
    assert deadline.total_timeout_seconds == 900.0
    assert deadline.timeout_for_next_chunk(now=100.0) == pytest.approx(30.0)
    deadline.note_chunk(now=120.0)
    assert deadline.timeout_for_next_chunk(now=121.0) == pytest.approx(29.0)
    deadline.note_chunk(now=990.0)
    assert deadline.timeout_for_next_chunk(now=999.0) == pytest.approx(1.0)

    idle_deadline = UploadDeadline(started_at=100.0)
    idle_deadline.note_chunk(now=120.0)
    with pytest.raises(UploadDeadlineExceeded) as idle:
        idle_deadline.check(now=150.01)
    assert idle.value.kind == "idle"

    total = UploadDeadline(started_at=0.0)
    total.note_chunk(now=899.9)
    with pytest.raises(UploadDeadlineExceeded) as exceeded:
        total.check(now=900.0)
    assert exceeded.value.kind == "total"
    assert exceeded.value.failure_code is ArtifactImportFailureCode.UPLOAD_INTERRUPTED


def test_next_chunk_helper_passes_the_smaller_deadline_to_wait_for() -> None:
    observed_timeouts: list[float] = []

    async def source():
        yield b"chunk"

    async def fake_wait_for(awaitable, timeout: float):
        observed_timeouts.append(timeout)
        return await awaitable

    iterator = source().__aiter__()
    deadline = UploadDeadline(started_at=0.0)
    deadline.note_chunk(now=870.0)
    chunk = asyncio.run(
        next_chunk_with_deadline(
            iterator,
            deadline,
            now=lambda: 880.0,
            wait_for=fake_wait_for,
        )
    )

    assert chunk == b"chunk"
    assert observed_timeouts == [pytest.approx(20.0)]


def test_digest_path_uses_two_fanout_levels_and_rejects_invalid_digest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    digest = "0123456789abcdef" * 4

    assert store.blob_path(digest) == (
        tmp_path / "artifacts" / "blobs" / "sha256" / "01" / "23" / digest
    )
    for invalid in ("../blob", "A" * 64, "0" * 63, "g" * 64):
        with pytest.raises(ValueError):
            store.blob_path(invalid)


def test_digest_lock_registry_releases_one_thousand_distinct_keys(tmp_path: Path) -> None:
    baseline = set(store_module._DIGEST_LOCKS)

    for index in range(1_000):
        with store_module._digest_lock(tmp_path / f"digest-{index}"):
            pass

    assert set(store_module._DIGEST_LOCKS) == baseline


def test_digest_lock_registry_releases_key_when_holder_raises(tmp_path: Path) -> None:
    baseline = set(store_module._DIGEST_LOCKS)

    with pytest.raises(RuntimeError, match="inside digest lock"):
        with store_module._digest_lock(tmp_path / "exceptional-digest"):
            raise RuntimeError("inside digest lock")

    assert set(store_module._DIGEST_LOCKS) == baseline


def test_digest_lock_waiter_shares_one_lock_and_registry_returns_to_baseline(
    tmp_path: Path,
) -> None:
    baseline = set(store_module._DIGEST_LOCKS)
    destination = tmp_path / "shared-digest"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def first_holder() -> None:
        try:
            with store_module._digest_lock(destination):
                first_entered.set()
                assert release_first.wait(timeout=1.0)
        except BaseException as error:
            errors.append(error)

    def second_holder() -> None:
        try:
            assert first_entered.wait(timeout=1.0)
            second_attempting.set()
            with store_module._digest_lock(destination):
                second_entered.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=first_holder)
    second = threading.Thread(target=second_holder)
    first.start()
    second.start()
    try:
        assert second_attempting.wait(timeout=1.0)
        assert not second_entered.wait(timeout=0.05)
    finally:
        release_first.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert second_entered.is_set()
    assert set(store_module._DIGEST_LOCKS) == baseline


def test_existing_good_blob_is_stream_verified_and_reused(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64, chunk_size=3)
    content = b"deduplicate-me"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)

    stored = store.promote(staged)

    assert stored.path == destination
    assert stored.reused is True
    assert stored.byte_size == len(content)
    assert destination.read_bytes() == content
    assert not staged.path.exists()


def test_existing_corrupt_blob_is_refused_not_overwritten(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64, chunk_size=2)
    content = b"expected-content"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    destination.parent.mkdir(parents=True)
    corrupt = b"X" * len(content)
    destination.write_bytes(corrupt)

    with pytest.raises(StoredBlobIntegrityError) as captured:
        store.promote(staged)

    assert captured.value.failure_code is ArtifactImportFailureCode.CORRUPT_MEDIA
    assert destination.read_bytes() == corrupt
    assert not staged.path.exists()


@pytest.mark.parametrize(
    "read_error",
    [
        PermissionError(errno.EACCES, "denied"),
        OSError(errno.EIO, "device read failed"),
        FileNotFoundError(errno.ENOENT, "raced away"),
    ],
)
def test_promote_read_failure_is_retryable_and_preserves_staging(
    tmp_path: Path,
    monkeypatch,
    read_error: OSError,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"retry-readable-staging"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    real_open = Path.open

    def failing_open(path: Path, *args, **kwargs):
        if path == staged.path and args and args[0] == "rb":
            read_error.filename = str(staged.path)
            raise read_error
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(Exception) as captured:
        store.promote(staged)

    _assert_storage_io_error(captured.value, staged.path)
    assert staged.path.read_bytes() == content


@pytest.mark.parametrize("error_number", [errno.EXDEV, errno.EACCES])
def test_promote_publish_failure_is_retryable_and_preserves_staging(
    tmp_path: Path,
    monkeypatch,
    error_number: int,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"retry-publish"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))

    def failing_publish(_source: Path, destination: Path) -> None:
        raise OSError(error_number, "publish failed", str(destination))

    monkeypatch.setattr(store_module, "_atomic_promote", failing_publish)

    with pytest.raises(Exception) as captured:
        store.promote(staged)

    _assert_storage_io_error(captured.value, store.blob_path(staged.sha256))
    assert staged.path.read_bytes() == content


@pytest.mark.parametrize("operation", ["discard", "cleanup"])
def test_staging_unlink_failure_is_typed_and_keeps_the_part(
    tmp_path: Path,
    monkeypatch,
    operation: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"retry-unlink"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    if operation == "cleanup":
        os.utime(staged.path, (1_000.0, 1_000.0))
    real_unlink = Path.unlink

    def failing_unlink(path: Path, *args, **kwargs) -> None:
        if path == staged.path:
            raise PermissionError(errno.EACCES, "unlink denied", str(path))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    with pytest.raises(Exception) as captured:
        if operation == "discard":
            store.discard(staged)
        else:
            store.cleanup_staging(max_age_seconds=3_600.0, now=5_000.0)

    _assert_storage_io_error(captured.value, staged.path)
    assert staged.path.read_bytes() == content


def test_posix_publish_race_never_overwrites_new_corrupt_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"content-addressed"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    corrupt = b"race-created-corrupt-destination"
    real_replace = os.replace
    real_link = os.link

    def racing_rename(source: Path, target: Path) -> None:
        target.write_bytes(corrupt)
        real_replace(source, target)

    def racing_link(source: Path, target: Path, *, follow_symlinks: bool) -> None:
        target.write_bytes(corrupt)
        real_link(source, target, follow_symlinks=follow_symlinks)

    class PosixOSProxy:
        name = "posix"
        rename = staticmethod(racing_rename)
        link = staticmethod(racing_link)

        def __getattr__(self, name: str):
            return getattr(os, name)

    monkeypatch.setattr(store_module, "os", PosixOSProxy())

    with pytest.raises(StoredBlobIntegrityError):
        store.promote(staged)

    assert destination.read_bytes() == corrupt
    assert not staged.path.exists()


def test_posix_publish_fsyncs_new_link_before_unlinking_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"durable-hard-link"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    events: list[tuple[str, Path]] = []
    real_link = os.link
    real_unlink = os.unlink

    class PosixOSProxy:
        name = "posix"

        def link(self, source: Path, target: Path, *, follow_symlinks: bool) -> None:
            events.append(("link", Path(target)))
            real_link(source, target, follow_symlinks=follow_symlinks)

        def unlink(self, path: Path) -> None:
            events.append(("unlink", Path(path)))
            real_unlink(path)

        def __getattr__(self, name: str):
            return getattr(os, name)

    def recording_directory_fsync(path: Path) -> None:
        events.append(("fsync", Path(path)))

    monkeypatch.setattr(store_module, "os", PosixOSProxy())
    monkeypatch.setattr(store_module, "_fsync_directory_best_effort", recording_directory_fsync)
    monkeypatch.setattr(
        store_module,
        "_fsync_directory_strict",
        recording_directory_fsync,
        raising=False,
    )

    stored = store.promote(staged)

    assert stored.path == destination
    assert events == [
        ("link", destination),
        ("fsync", destination.parent),
        ("unlink", staged.path),
        ("fsync", staged.path.parent),
    ]


def test_posix_destination_directory_fsync_failure_preserves_staging_hardlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"destination-fsync-retry"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    descriptor_target = tmp_path / "directory-fsync-target"
    descriptor_target.write_bytes(b"fsync")
    real_open = os.open
    real_fsync = os.fsync
    real_link = os.link
    fsynced_directories: list[Path] = []
    active_directory: Path | None = None

    class PosixOSProxy:
        name = "posix"

        def link(self, source: Path, target: Path, *, follow_symlinks: bool) -> None:
            real_link(source, target, follow_symlinks=follow_symlinks)

        def open(self, path: Path, _flags: int) -> int:
            nonlocal active_directory
            active_directory = Path(path)
            return real_open(descriptor_target, os.O_RDWR)

        def fsync(self, descriptor: int) -> None:
            assert active_directory is not None
            fsynced_directories.append(active_directory)
            if len(fsynced_directories) == 1:
                raise OSError(
                    errno.EIO,
                    "destination directory fsync failed",
                    str(destination.parent),
                )
            real_fsync(descriptor)

        def __getattr__(self, name: str):
            return getattr(os, name)

    monkeypatch.setattr(store_module, "os", PosixOSProxy())

    with pytest.raises(Exception) as captured:
        store.promote(staged)

    _assert_storage_io_error(captured.value, destination.parent)
    assert staged.path.read_bytes() == content
    assert destination.read_bytes() == content
    assert os.path.samefile(staged.path, destination)

    stored = store.promote(staged)

    assert stored.reused is True
    assert not staged.path.exists()
    assert destination.read_bytes() == content
    assert fsynced_directories == [
        destination.parent,
        destination.parent,
        staged.path.parent,
    ]


def test_posix_staging_directory_fsync_failure_reports_safe_published_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"staging-fsync-retry"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    descriptor_target = tmp_path / "directory-fsync-target"
    descriptor_target.write_bytes(b"fsync")
    real_open = os.open
    real_fsync = os.fsync
    real_link = os.link
    real_unlink = os.unlink
    fsync_calls = 0

    class PosixOSProxy:
        name = "posix"

        def link(self, source: Path, target: Path, *, follow_symlinks: bool) -> None:
            real_link(source, target, follow_symlinks=follow_symlinks)

        def unlink(self, path: Path) -> None:
            real_unlink(path)

        def open(self, _path: Path, flags: int) -> int:
            return real_open(descriptor_target, os.O_RDWR)

        def fsync(self, descriptor: int) -> None:
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise OSError(
                    errno.EIO,
                    "staging directory fsync failed",
                    str(staged.path.parent),
                )
            real_fsync(descriptor)

        def __getattr__(self, name: str):
            return getattr(os, name)

    monkeypatch.setattr(store_module, "os", PosixOSProxy())

    with pytest.raises(Exception) as captured:
        store.promote(staged)

    _assert_storage_io_error(captured.value, staged.path.parent)
    assert fsync_calls == 2, repr(captured.value.__cause__)
    assert not staged.path.exists()
    assert destination.read_bytes() == content


def test_posix_crash_before_staging_unlink_leaves_two_safe_hard_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"safe-double-link"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)
    real_link = os.link

    class SimulatedCrash(OSError):
        pass

    class PosixOSProxy:
        name = "posix"

        def link(self, source: Path, target: Path, *, follow_symlinks: bool) -> None:
            real_link(source, target, follow_symlinks=follow_symlinks)

        def unlink(self, _path: Path) -> None:
            raise SimulatedCrash("crash before staging unlink")

        def __getattr__(self, name: str):
            return getattr(os, name)

    monkeypatch.setattr(store_module, "os", PosixOSProxy())
    monkeypatch.setattr(store_module, "_fsync_directory_strict", lambda _path: None, raising=False)

    with pytest.raises(Exception) as captured:
        store.promote(staged)

    _assert_storage_io_error(captured.value, staged.path)
    assert staged.path.read_bytes() == content
    assert destination.read_bytes() == content
    assert os.path.samefile(staged.path, destination)


def test_cleanup_staging_removes_only_old_canonical_uuid_parts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.staging_root.mkdir(parents=True)
    old = store.staging_root / f"{uuid4()}.part"
    fresh = store.staging_root / f"{uuid4()}.part"
    misleading = store.staging_root / "not-a-uuid.part"
    nested = store.staging_root / "nested" / f"{uuid4()}.part"
    for path in (old, fresh, misleading, nested):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    os.utime(old, (1_000.0, 1_000.0))
    os.utime(fresh, (4_000.0, 4_000.0))
    os.utime(misleading, (1_000.0, 1_000.0))
    os.utime(nested, (1_000.0, 1_000.0))

    removed = store.cleanup_staging(max_age_seconds=3_600.0, now=5_000.0)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
    assert misleading.exists()
    assert nested.exists()


def test_failure_injected_after_promote_leaves_durable_unreferenced_blob(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"promoted-before-database"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    destination = store.blob_path(staged.sha256)

    def fail_after_promote(path: Path) -> None:
        assert path == destination
        assert path.read_bytes() == content
        raise RuntimeError("simulated crash before database commit")

    with pytest.raises(RuntimeError, match="simulated crash"):
        store.promote(staged, after_promote=fail_after_promote)

    assert destination.read_bytes() == content
    assert not staged.path.exists()


def test_after_promote_callback_runs_after_digest_lock_release(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"callback-outside-lock"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    baseline = set(store_module._DIGEST_LOCKS)
    observed_registry: list[set[str]] = []

    def observe_registry(_path: Path) -> None:
        observed_registry.append(set(store_module._DIGEST_LOCKS))

    stored = store.promote(staged, after_promote=observe_registry)

    assert stored.path.read_bytes() == content
    assert observed_registry == [baseline]


def test_remove_blob_deletes_only_the_verified_cas_object(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64, chunk_size=4)
    content = b"discard-this-object"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    stored = store.promote(staged)
    neighbour = stored.path.parent / "keep-me"
    neighbour.write_bytes(b"unrelated")

    assert store.remove_blob(stored.sha256, stored.byte_size) is True
    assert not stored.path.exists()
    assert neighbour.read_bytes() == b"unrelated"
    assert store.remove_blob(stored.sha256, stored.byte_size) is False


@pytest.mark.parametrize("digest", ["", "A" * 64, "../outside", "0" * 63])
def test_remove_blob_rejects_invalid_digest_without_mutation(
    tmp_path: Path,
    digest: str,
) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"keep")

    with pytest.raises(ValueError):
        store.remove_blob(digest, 1)

    assert sentinel.read_bytes() == b"keep"


def test_remove_blob_refuses_corrupt_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=64)
    content = b"verified-content"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    stored = store.promote(staged)
    stored.path.write_bytes(b"tampered-content")

    with pytest.raises(StoredBlobIntegrityError):
        store.remove_blob(stored.sha256, stored.byte_size)

    assert stored.path.read_bytes() == b"tampered-content"


def test_remove_blob_refuses_reparse_ancestor_without_external_deletion(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    data_dir.mkdir()
    content = b"external-cas-content"
    digest = hashlib.sha256(content).hexdigest()
    external_blob = external / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
    external_blob.parent.mkdir(parents=True)
    external_blob.write_bytes(content)
    artifacts_link = data_dir / "artifacts"
    _create_directory_link(artifacts_link, external)
    store = ArtifactStore(data_dir)

    try:
        with pytest.raises(UnsafeStagingPathError):
            store.remove_blob(digest, len(content))
        assert external_blob.read_bytes() == content
    finally:
        _remove_directory_link(artifacts_link)


def test_discard_refuses_non_generated_or_foreign_staging_paths(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    victim = tmp_path / "do-not-delete"
    victim.write_bytes(b"important")
    forged = StagedArtifact(uuid4(), victim, "0" * 64, len(b"important"))

    with pytest.raises(UnsafeStagingPathError):
        store.discard(forged)

    assert victim.read_bytes() == b"important"


@pytest.mark.parametrize(
    ("untrusted", "expected"),
    [
        ("../relative/camera.png", "camera.png"),
        ("/absolute/path/camera.png", "camera.png"),
        (r"C:\absolute\camera.png", "camera.png"),
        (r"\\server\share\camera.png", "camera.png"),
        ("photo.png:payload.exe", "photo.png_payload.exe"),
        ("bad\x00name\u202egnp.exe.png", "badnamegnp.exe.png"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("... ", "upload"),
    ],
)
def test_filename_sanitization_never_preserves_path_or_windows_metacharacters(
    untrusted: str,
    expected: str,
) -> None:
    sanitized = sanitize_filename(untrusted)

    assert sanitized == expected
    assert not any(character in sanitized for character in '<>:"/\\|?*')
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in sanitized)
    assert len(sanitized) <= 255


def test_staging_paths_are_canonical_uuid_names(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    import_id = UUID("12345678-1234-5678-1234-567812345678")

    assert store.staging_path(import_id).name == "12345678-1234-5678-1234-567812345678.part"


def test_verify_blob_reuses_the_trusted_cas_integrity_boundary(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    content = b"startup-integrity"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    promoted = store.promote(staged)

    verified = store.verify_blob(promoted.sha256, promoted.byte_size)

    assert verified.path == promoted.path
    assert verified.sha256 == promoted.sha256
    assert verified.byte_size == promoted.byte_size
    assert verified.reused is True


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_verify_blob_fails_closed_without_deleting_damaged_content(
    tmp_path: Path,
    damage: str,
) -> None:
    store = ArtifactStore(tmp_path)
    content = b"startup-integrity"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    promoted = store.promote(staged)
    if damage == "missing":
        promoted.path.unlink()
    else:
        promoted.path.write_bytes(b"damaged")

    with pytest.raises(StoredBlobIntegrityError):
        store.verify_blob(promoted.sha256, promoted.byte_size)

    if damage == "corrupt":
        assert promoted.path.read_bytes() == b"damaged"


def test_read_blob_returns_only_fully_verified_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    content = b"authenticated-full-body"
    promoted = store.promote(
        store.stage_chunks(uuid4(), [content], declared_size=len(content))
    )

    assert store.read_blob(promoted.sha256, promoted.byte_size) == content


def test_read_blob_rejects_and_preserves_corrupt_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    content = b"authenticated-full-body"
    promoted = store.promote(
        store.stage_chunks(uuid4(), [content], declared_size=len(content))
    )
    promoted.path.write_bytes(b"corrupt")

    with pytest.raises(StoredBlobIntegrityError):
        store.read_blob(promoted.sha256, promoted.byte_size)

    assert promoted.path.read_bytes() == b"corrupt"
