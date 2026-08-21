"""PR15 managed generation publication, activation, recovery, and serving tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from managed_v2_test_support import (
    RealManagedV2Scenario,
    build_real_managed_v2_scenario,
    build_real_managed_v2_variant,
    clone_real_managed_v2_scenario,
)
from typer.testing import CliRunner

from mastervault.change_control import (
    managed_generation_repository as generation_repository_module,
)
from mastervault.change_control.application import ChangeControlApplication
from mastervault.change_control.managed_activation_service import (
    ManagedActivationBackendUnsupportedError,
    ManagedActivationOutcome,
    ManagedActivationServiceError,
    activate_reviewed_managed_generation,
)
from mastervault.change_control.managed_generation import (
    INDEX_COUNT_KEYS_V1,
    ManagedActivationCommand,
    ManagedIndexReadinessReceipt,
)
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepository,
    ManagedGenerationRepositoryError,
    RepositoryVerifiedManagedGenerationEffects,
)
from mastervault.change_control.managed_query_resolver import (
    build_read_only_managed_query_resolver,
    reopen_sealed_seed_query_bootstrap,
)
from mastervault.change_control.managed_review import (
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    NoChangeImpactCard,
    PublicationKind,
)
from mastervault.change_control.managed_review_service import (
    ManagedRevisionReviewSelection,
    decide_managed_revision_review,
    open_managed_revision_review,
)
from mastervault.change_control.managed_serving import (
    ManagedServingError,
    open_active_managed_sqlite_generation,
    open_active_managed_sqlite_index,
)
from mastervault.change_control.managed_store import (
    ManagedGenerationActivationError,
    ManagedGenerationActivationStaleError,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.query_generation import QueryGenerationKind
from mastervault.change_control.store import (
    ChangeControlBusyError,
    ChangeControlIdempotencyError,
)
from mastervault.config import Settings
from mastervault.pipelines.ask import run_ask
from mastervault.providers import MockEmbedding, MockLLM
from mastervault.retrieval import hybrid_search
from mastervault.storage.sqlite import SqliteBackend


@pytest.mark.generation_activation_d
def test_generation_repository_rejects_protected_nesting_before_creation(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    candidate = protected / "generation-effects"

    with pytest.raises(ManagedGenerationRepositoryError, match="disjoint"):
        ManagedGenerationRepository(candidate, forbidden_roots=(protected,))

    assert not candidate.exists()


@pytest.mark.generation_activation_d
def test_generation_repository_mode_change_fails_before_index_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "generation-effects"
    repository = ManagedGenerationRepository(root)
    root.chmod(0o755)

    with pytest.raises(ManagedGenerationRepositoryError, match="substituted"):
        repository._ensure_index_file(
            "generations/mgeneration:" + "0" * 64 + "/index/mastervault.sqlite3"
        )

    assert not (root / "generations").exists()


@pytest.mark.generation_activation_d
def test_generation_repository_rejects_case_alias_of_protected_root(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "ProtectedRoot"
    protected.mkdir(mode=0o700)
    alias = tmp_path / "protectedroot"
    if not alias.exists():
        pytest.skip("filesystem is case-sensitive")
    assert alias.samefile(protected)

    with pytest.raises(ManagedGenerationRepositoryError, match="disjoint"):
        ManagedGenerationRepository(alias, forbidden_roots=(protected,))

    assert not (protected / ".evidence.lock").exists()


@pytest.mark.generation_activation_d
def test_generation_repository_creation_fsyncs_new_root_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    synchronized: list[tuple[int, int]] = []
    original_fsync = os.fsync

    def record_fsync(fd: int) -> None:
        info = os.fstat(fd)
        synchronized.append((info.st_dev, info.st_ino))
        original_fsync(fd)

    with monkeypatch.context() as observed:
        observed.setattr(generation_repository_module.os, "fsync", record_fsync)
        ManagedGenerationRepository(tmp_path / "generation-effects")

    assert parent_identity in synchronized


@pytest.mark.generation_activation_d
def test_generation_repository_read_only_open_cannot_create_or_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "generation-effects"
    ManagedGenerationRepository(root)
    before = tuple(
        (path.relative_to(root).as_posix(), path.lstat().st_mode, path.lstat().st_size)
        for path in sorted(root.rglob("*"))
    )

    def forbidden_effect(*_args: object, **_kwargs: object) -> None:
        pytest.fail("read-only generation repository attempted a filesystem effect")

    with monkeypatch.context() as guarded:
        guarded.setattr(generation_repository_module.os, "mkdir", forbidden_effect)
        guarded.setattr(
            generation_repository_module.os,
            "supports_dir_fd",
            {*os.supports_dir_fd, forbidden_effect},
        )
        guarded.setattr(generation_repository_module.os, "fsync", forbidden_effect)
        repository = ManagedGenerationRepository(
            root,
            create=False,
            read_only=True,
        )

    assert repository.read_only
    with pytest.raises(ManagedGenerationRepositoryError, match="rejects mutation"):
        repository._ensure_index_file("generations/example/index/mastervault.sqlite3")
    assert tuple(
        (path.relative_to(root).as_posix(), path.lstat().st_mode, path.lstat().st_size)
        for path in sorted(root.rglob("*"))
    ) == before


@pytest.mark.generation_activation_d
def test_pinned_index_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    repository = ManagedGenerationRepository(tmp_path / "generation-effects")
    generation_id = "mgeneration:" + "a" * 64
    relative = repository.index_relative_path(generation_id=generation_id)
    parent_fd, name = repository._backend._open_parent(relative, create=True)
    try:
        os.mkfifo(name, mode=0o600, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)

    with pytest.raises(ManagedGenerationRepositoryError, match="regular inode"):
        repository._open_pinned_index(relative, writable=False)


@pytest.mark.generation_activation_d
def test_reported_sqlite_locator_uses_inode_identity_not_path_text(tmp_path: Path) -> None:
    repository = ManagedGenerationRepository(tmp_path / "generation-effects")
    generation_id = "mgeneration:" + "b" * 64
    relative = repository.index_relative_path(generation_id=generation_id)
    index_path, _created = repository._ensure_index_file(relative)
    pinned = repository._open_pinned_index(relative, writable=True)
    builder = SqliteBackend(":memory:")
    try:
        with builder.conn:
            builder.conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            builder.conn.execute("INSERT INTO marker(value) VALUES ('exact')")
        image = builder.conn.serialize(name="main")
    finally:
        builder.close()
    try:
        pinned.write_image(image)
        descriptor_alias = repository._descriptor_alias(pinned)
        assert descriptor_alias != index_path
        assert repository._verify_reported_sqlite_locator(
            reported=index_path,
            pinned=pinned,
        ) == generation_repository_module._stable_file_signature(
            pinned.verify_entry(allow_empty=False)
        )

        substituted = tmp_path / "substituted.sqlite3"
        substituted.write_bytes(image)
        substituted.chmod(0o400)
        with pytest.raises(ManagedGenerationRepositoryError, match="not the pinned"):
            repository._verify_reported_sqlite_locator(
                reported=substituted,
                pinned=pinned,
            )
    finally:
        pinned.close()


@pytest.mark.generation_activation_d
def test_pinned_index_write_and_open_ignore_intermediate_directory_swap(
    tmp_path: Path,
) -> None:
    repository = ManagedGenerationRepository(tmp_path / "generation-effects")
    generation_id = "mgeneration:" + "0" * 64
    relative = repository.index_relative_path(generation_id=generation_id)
    index_path, _created = repository._ensure_index_file(relative)
    pinned = repository._open_pinned_index(relative, writable=True)
    builder = SqliteBackend(":memory:")
    try:
        with builder.conn:
            builder.conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            builder.conn.execute("INSERT INTO marker(value) VALUES ('pinned')")
        image = builder.conn.serialize(name="main")
    finally:
        builder.close()

    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    victim = protected / index_path.name
    victim_backend = SqliteBackend(victim)
    try:
        with victim_backend.conn:
            victim_backend.conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            victim_backend.conn.execute("INSERT INTO marker(value) VALUES ('victim')")
    finally:
        victim_backend.close()
    victim.chmod(0o600)
    victim_before = victim.read_bytes()

    moved_parent = index_path.parent.with_name("index-pinned")
    index_path.parent.rename(moved_parent)
    index_path.parent.symlink_to(protected, target_is_directory=True)
    try:
        pinned.write_image(image)
        backend = repository._open_sqlite_backend(pinned)
        backend_guard = vars(backend)["_managed_index_guard"]
        guarded_fds = (backend_guard.file_fd, backend_guard.parent_fd)
        try:
            assert backend.conn.execute("SELECT value FROM marker").fetchone()[0] == "pinned"
            for fd in guarded_fds:
                os.fstat(fd)
        finally:
            backend.close()
        for fd in guarded_fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert (moved_parent / index_path.name).read_bytes() == image
        assert victim.read_bytes() == victim_before
    finally:
        pinned.close()
        index_path.parent.unlink()
        moved_parent.rename(index_path.parent)


@pytest.mark.generation_activation_d
def test_index_swap_during_ready_commit_cannot_leave_poisoned_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ManagedGenerationRepository(tmp_path / "generation-effects")
    generation_id = "mgeneration:" + "1" * 64
    relative = repository.index_relative_path(generation_id=generation_id)
    repository._ensure_index_file(relative)
    pinned = repository._open_pinned_index(relative, writable=True)
    builder = SqliteBackend(":memory:")
    try:
        with builder.conn:
            builder.conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        pinned.write_image(builder.conn.serialize(name="main"))
    finally:
        builder.close()

    original_link = os.link
    displaced_name = "mastervault-displaced.sqlite3"

    def swap_before_ready_link(
        source: str,
        destination: str,
        **kwargs: object,
    ) -> None:
        os.rename(
            pinned.name,
            displaced_name,
            src_dir_fd=pinned.parent_fd,
            dst_dir_fd=pinned.parent_fd,
        )
        replacement = os.open(
            pinned.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=pinned.parent_fd,
        )
        os.close(replacement)
        original_link(source, destination, **kwargs)

    ready_relative = repository.index_readiness_relative_path(generation_id=generation_id)
    try:
        with monkeypatch.context() as swapped:
            swapped.setattr(generation_repository_module.os, "link", swap_before_ready_link)
            with pytest.raises(
                ManagedGenerationRepositoryError,
                match="pinned inode",
            ):
                repository._create_index_readiness(
                    pinned=pinned,
                    relative=ready_relative,
                    content=b"{}",
                )
        names = os.listdir(pinned.parent_fd)
        assert "READY.json" not in names
        assert not any(name.startswith("pending-") for name in names)
    finally:
        with pytest.raises(ManagedGenerationRepositoryError):
            pinned.verify_entry(allow_empty=False)
        os.unlink(pinned.name, dir_fd=pinned.parent_fd)
        os.rename(
            displaced_name,
            pinned.name,
            src_dir_fd=pinned.parent_fd,
            dst_dir_fd=pinned.parent_fd,
        )
        pinned.close()


@pytest.mark.generation_activation_d
def test_publication_and_readiness_reopen_ignore_intermediate_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ManagedGenerationRepository(tmp_path / "generation-effects")
    generation_id = "mgeneration:" + "2" * 64
    publication_relative = (
        f"generations/{generation_id}/canonical/support/customer-faq.md"
    )
    publication_content = b"reviewed publication\n"
    repository._backend._create_only(
        publication_relative,
        publication_content,
        label="test publication",
    )
    readiness = ManagedIndexReadinessReceipt.create(
        activation_id="mactivation:" + "3" * 64,
        generation_id=generation_id,
        manifest_sha256="4" * 64,
        projection_id="mgenerationprojection:" + "5" * 64,
        projection_sha256="6" * 64,
        serving_content_fingerprint="7" * 64,
        index_relative_path=repository.index_relative_path(generation_id=generation_id),
        index_file_sha256="8" * 64,
        index_file_byte_count=1,
        logical_index_fingerprint="9" * 64,
        storage_schema_version=1,
        embedding_model_version="test-embedding-v1",
        embedding_dimensions=3,
        counts=tuple((name, 0) for name in INDEX_COUNT_KEYS_V1),
        ready_at="2026-08-10T00:00:00+00:00",
    )
    readiness_relative = repository.index_readiness_relative_path(generation_id=generation_id)
    repository._backend._create_only(
        readiness_relative,
        canonical_json_bytes(readiness.model_dump(mode="json")),
        label="test readiness",
    )

    def exercise_swap(relative: str, read: Callable[[], object]) -> object:
        canonical_parent = repository.root / Path(relative).parent
        moved_parent = canonical_parent.with_name(canonical_parent.name + "-pinned")
        victim_parent = tmp_path / (canonical_parent.name + "-victim")
        victim_parent.mkdir(mode=0o700)
        victim_leaf = victim_parent / Path(relative).name
        victim_leaf.write_bytes(b"redirected outside repository\n")
        victim_leaf.chmod(0o600)
        original_open_parent = repository._backend._open_parent
        swapped = False

        def swap_after_parent_pin(value: str, *, create: bool) -> tuple[int, str]:
            nonlocal swapped
            parent_fd, name = original_open_parent(value, create=create)
            if value == relative and not swapped:
                canonical_parent.rename(moved_parent)
                canonical_parent.symlink_to(victim_parent, target_is_directory=True)
                swapped = True
            return parent_fd, name

        try:
            with monkeypatch.context() as context:
                context.setattr(repository._backend, "_open_parent", swap_after_parent_pin)
                result = read()
            assert swapped
            assert victim_leaf.read_bytes() == b"redirected outside repository\n"
            return result
        finally:
            if canonical_parent.is_symlink():
                canonical_parent.unlink()
            if moved_parent.exists():
                moved_parent.rename(canonical_parent)

    assert exercise_swap(
        publication_relative,
        lambda: repository._read_exact(
            publication_relative,
            limit=len(publication_content),
        ),
    ) == publication_content
    assert exercise_swap(
        readiness_relative,
        lambda: repository._read_index_readiness(generation_id=generation_id),
    ) == readiness


@pytest.fixture(scope="module")
def generation_seed(tmp_path_factory: pytest.TempPathFactory) -> RealManagedV2Scenario:
    scenario = build_real_managed_v2_scenario(tmp_path_factory.mktemp("managed-generation-seed"))
    scenario.store.close()
    return scenario


def _decide(
    scenario: RealManagedV2Scenario,
    *,
    prefix: str,
    mode: str,
) -> str:
    opened = open_managed_revision_review(
        store=scenario.store,
        run_binding=scenario.run_binding,
        admitted_subjects=scenario.subjects,
        reviewed_snapshot=scenario.reviewed_snapshot,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
        operation_id=f"{prefix}:open",
        requester_id="operator@example.test",
        rationale=f"Open the complete reviewed generation for {prefix}.",
    )
    selections = []
    for target in opened.request_record.command.bundle.targets:
        if mode == "reject-all":
            disposition = ManagedRevisionDisposition.REJECT
        elif isinstance(target.subject, ManagedRevisionPlan):
            disposition = (
                ManagedRevisionDisposition.APPROVE
                if mode == "mixed"
                else ManagedRevisionDisposition.REJECT
            )
        elif isinstance(target.subject, NoChangeImpactCard):
            disposition = ManagedRevisionDisposition.CONFIRM_NO_CHANGE
        else:  # pragma: no cover - fixture subject union guard
            raise AssertionError("unexpected managed review subject")
        selections.append(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=disposition,
            )
        )
    decided = decide_managed_revision_review(
        store=scenario.store,
        request_id=opened.request_record.command.request_id,
        selections=tuple(selections),
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
        operation_id=f"{prefix}:decision",
        reviewer_id="reviewer@example.test",
        rationale=f"Record the exact managed generation outcome for {prefix}.",
    )
    assert decided.decision_record is not None
    return decided.decision_record.command.request_record.command.request_id


def _activate(
    scenario: RealManagedV2Scenario,
    *,
    request_id: str,
    operation_id: str,
    generation_root: Path,
    failure_hook=None,
    embedder: MockEmbedding | None = None,
):
    return activate_reviewed_managed_generation(
        request_id=request_id,
        operation_id=operation_id,
        store=scenario.store,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
        generation_root=generation_root,
        embedder=embedder or MockEmbedding(8),
        failure_hook=failure_hook,
    )


@pytest.mark.generation_activation_a
def test_mixed_generation_publishes_indexes_activates_and_serves_exactly(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:mixed", mode="mixed")
        generation_root = tmp_path / "generations"
        embedder = MockEmbedding(8)
        embed_calls = 0
        original_embed = embedder.embed

        def count_embed(texts: list[str]) -> list[list[float]]:
            nonlocal embed_calls
            embed_calls += 1
            return original_embed(texts)

        monkeypatch.setattr(embedder, "embed", count_embed)
        result = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:mixed:activate",
            generation_root=generation_root,
            embedder=embedder,
        )
        first_state = scenario.store.get_managed_generation_activation(
            "generation:mixed:activate",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert first_state is not None and first_state.index_receipt is not None
        first_index_path = generation_root / first_state.index_receipt.index_relative_path
        first_index_stat = first_index_path.stat()
        first_index_sha = hashlib.sha256(first_index_path.read_bytes()).hexdigest()
        replay = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:mixed:activate",
            generation_root=generation_root,
            embedder=embedder,
        )

        assert result == replay
        assert result.outcome == ManagedActivationOutcome.ACTIVATED
        assert result.command is not None and result.receipt is not None
        assert result.receipt.publication_count > 0
        replayed_index_stat = first_index_path.stat()
        assert embed_calls == 1
        assert (
            replayed_index_stat.st_dev,
            replayed_index_stat.st_ino,
            replayed_index_stat.st_size,
            replayed_index_stat.st_mtime_ns,
            replayed_index_stat.st_ctime_ns,
        ) == (
            first_index_stat.st_dev,
            first_index_stat.st_ino,
            first_index_stat.st_size,
            first_index_stat.st_mtime_ns,
            first_index_stat.st_ctime_ns,
        )
        assert hashlib.sha256(first_index_path.read_bytes()).hexdigest() == first_index_sha
        entries_by_version = {
            item.document.document_version_id: item for item in result.command.projection.entries
        }
        approved_plans = tuple(
            item for item in scenario.subjects if isinstance(item, ManagedRevisionPlan)
        )
        assert approved_plans
        for plan in approved_plans:
            predecessor = entries_by_version[plan.predecessor.document_version_id]
            successor = entries_by_version[plan.successor.document_version_id]
            assert not predecessor.included_in_serving_index
            assert successor.included_in_serving_index
            assert predecessor.logical_path == successor.logical_path
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_revision_publication_events"
            ).fetchone()[0]
            == result.receipt.publication_count
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_activation_receipts"
            ).fetchone()[0]
            == 1
        )
        serving = open_active_managed_sqlite_generation(
            aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
            store=scenario.store,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            generation_root=generation_root,
        )
        try:
            assert serving.authority == result.receipt.activated_authority
            assert serving.activation_state == first_state
            assert serving.index_receipt == first_state.index_receipt
            assert tuple(note.entry for note in serving.resolved_notes) == (
                result.command.projection.entries
            )
            assert {note.workspace for note in serving.resolved_notes} == {
                scenario.canonical_root.resolve(),
                (
                    generation_root
                    / "generations"
                    / result.command.projection.generation_id
                    / "canonical"
                ).resolve(),
            }
            backend = serving.backend
            paths = tuple(
                str(row[0])
                for row in backend.conn.execute("SELECT rel_path FROM documents ORDER BY rel_path")
            )
            assert paths == tuple(
                item.logical_path
                for item in result.command.projection.entries
                if item.included_in_serving_index
            )
            with pytest.raises(sqlite3.OperationalError):
                backend.conn.execute("DELETE FROM documents")

            original_index = first_index_path.read_bytes()
            first_index_path.chmod(0o600)
            with first_index_path.open("r+b") as stream:
                stream.seek(100)
                original_byte = stream.read(1)
                stream.seek(100)
                stream.write(bytes([original_byte[0] ^ 1]))
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(100)
                stream.write(original_byte)
                stream.flush()
                os.fsync(stream.fileno())
            first_index_path.chmod(0o400)
            assert first_index_path.read_bytes() == original_index
            with pytest.raises(ManagedServingError, match="changed while serving"):
                serving.close()
        finally:
            serving.close()

        parent_backend = open_active_managed_sqlite_index(
            aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
            store=scenario.store,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            generation_root=generation_root,
        )
        index_parent = first_index_path.parent
        moved_index_parent = index_parent.with_name("index.pinned-original")
        index_parent.replace(moved_index_parent)
        index_parent.mkdir(mode=0o700)
        for source in moved_index_parent.iterdir():
            if source.is_file():
                shutil.copy2(source, index_parent / source.name)
        try:
            with pytest.raises(ManagedServingError, match="changed while serving"):
                parent_backend.close()
        finally:
            parent_backend.close()
            shutil.rmtree(index_parent)
            moved_index_parent.replace(index_parent)

        original_active_state = scenario.store.get_active_managed_generation_state
        active_state_reads = 0

        def supersede_after_index_open(*args: object, **kwargs: object) -> object | None:
            nonlocal active_state_reads
            active_state_reads += 1
            active = original_active_state(*args, **kwargs)
            return active if active_state_reads == 1 else None

        with monkeypatch.context() as concurrent_authority:
            concurrent_authority.setattr(
                scenario.store,
                "get_active_managed_generation_state",
                supersede_after_index_open,
            )
            with pytest.raises(ManagedServingError, match="changed during"):
                open_active_managed_sqlite_index(
                    aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                    store=scenario.store,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                    generation_root=generation_root,
                )
        assert active_state_reads == 2

        missing_root = tmp_path / "missing-generation-root"
        with pytest.raises(ManagedServingError):
            open_active_managed_sqlite_index(
                aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                store=scenario.store,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                generation_root=missing_root,
            )
        assert not missing_root.exists()
        state = scenario.store.get_managed_generation_activation(
            "generation:mixed:activate",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert state is not None and state.index_receipt is not None
        raw_event = next(
            item
            for item in state.publication_events
            if item.publication.destination.kind == PublicationKind.RAW_SOURCE
        )
        publication_path = generation_root / raw_event.repository_relative_path
        missing_publication = publication_path.with_name(f"{publication_path.name}.missing")
        publication_path.replace(missing_publication)
        try:
            with pytest.raises(ManagedServingError):
                open_active_managed_sqlite_index(
                    aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                    store=scenario.store,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                    generation_root=generation_root,
                )
        finally:
            missing_publication.replace(publication_path)

        index_path = generation_root / state.index_receipt.index_relative_path
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = index_path.with_name(index_path.name + suffix)
            sidecar.write_bytes(b"unsafe residue")
            try:
                with pytest.raises(ManagedServingError):
                    open_active_managed_sqlite_index(
                        aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                        store=scenario.store,
                        resolver=scenario.resolver,
                        verified_bootstrap=scenario.verified_bootstrap,
                        prechange_head=scenario.prechange_head,
                        generation_root=generation_root,
                    )
            finally:
                sidecar.unlink()

        hardlink = tmp_path / "managed-index-hardlink.sqlite3"
        os.link(index_path, hardlink)
        try:
            with pytest.raises(ManagedServingError):
                open_active_managed_sqlite_index(
                    aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                    store=scenario.store,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                    generation_root=generation_root,
                )
        finally:
            hardlink.unlink()

        missing_index = index_path.with_name(f"{index_path.name}.missing")
        index_path.replace(missing_index)
        try:
            with pytest.raises(ManagedServingError):
                open_active_managed_sqlite_index(
                    aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                    store=scenario.store,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                    generation_root=generation_root,
                )
        finally:
            missing_index.replace(index_path)
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_d
def test_active_managed_resolver_restarts_from_query_only_durable_evidence(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(
        generation_seed,
        tmp_path / "restart-scenario",
    )
    query_workspace = tmp_path / "query-workspace"
    change_control_root = query_workspace / "change_control"
    change_control_root.mkdir(parents=True, mode=0o700)
    (query_workspace / "vault").mkdir(mode=0o700)
    generation_root = change_control_root / "generations"

    def tree_signature(
        root: Path,
    ) -> tuple[tuple[str, int, int, int, int, int, int, str | None], ...]:
        result = []
        for path in sorted(root.rglob("*")):
            info = path.lstat()
            result.append(
                (
                    path.relative_to(root).as_posix(),
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    (
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        if stat.S_ISREG(info.st_mode)
                        else None
                    ),
                )
            )
        return tuple(result)

    try:
        request_id = _decide(
            scenario,
            prefix="generation:query-restart",
            mode="mixed",
        )
        activated = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:query-restart:activate",
            generation_root=generation_root,
            embedder=MockEmbedding(),
        )
        assert activated.command is not None
        scenario.store.close()

        secure_parent = tmp_path / "query-only-authority"
        secure_parent.mkdir(mode=0o700)
        secure_authority = secure_parent / "change-control.sqlite3"
        shutil.copyfile(scenario.authority_path, secure_authority)
        secure_authority.chmod(0o600)
        authority_before = secure_authority.read_bytes()
        authority_info = secure_authority.stat()
        authority_identity_before = (
            authority_info.st_dev,
            authority_info.st_ino,
            authority_info.st_mode,
            authority_info.st_nlink,
            authority_info.st_size,
            authority_info.st_mtime_ns,
            authority_info.st_ctime_ns,
        )
        evidence_before = tree_signature(scenario.evidence_root)
        canonical_before = tree_signature(scenario.canonical_root)

        operation_prefix = "temporal-commit:"
        assert scenario.run_binding.operation_id.startswith(operation_prefix)
        temporal_sha256 = scenario.run_binding.operation_id.removeprefix(
            operation_prefix
        )
        bootstrap = reopen_sealed_seed_query_bootstrap(
            seed_repository_root=Path(__file__).resolve().parents[3],
            evidence_repository_root=scenario.evidence_root,
            temporal_analysis_manifest_sha256=temporal_sha256,
        )
        store = SqliteManagedChangeControlStore(
            secure_authority,
            secure_open=True,
            read_only=True,
        )
        try:
            assert store.conn.execute("PRAGMA query_only").fetchone()[0] == 1
            active_decision = store.get_active_managed_decision_record(
                scenario.run_binding.prechange_head.aggregate_id,
                authority_context=bootstrap.authority_context,
            )
            assert active_decision is not None
            restarted = build_read_only_managed_query_resolver(
                store=store,
                active_decision=active_decision,
                bootstrap=bootstrap,
                canonical_repository_root=scenario.canonical_root,
            )
            assert restarted.active_decision == active_decision
            assert restarted.reviewed_snapshot.binding == (
                scenario.reviewed_snapshot.binding
            )
            assert (
                restarted.resolver.resolve_revision_planning_admission(
                    scenario.run_binding.revision_planning_admission
                )
                == scenario.run_binding.revision_planning_admission
            )
            assert (
                restarted.resolver.resolve_governing_source_adoption(
                    scenario.run_binding.governing_source_adoption
                )
                == scenario.run_binding.governing_source_adoption
            )
            assert store.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            store.close()

        authority_after = secure_authority.read_bytes()
        authority_info = secure_authority.stat()
        assert authority_after == authority_before
        assert (
            authority_info.st_dev,
            authority_info.st_ino,
            authority_info.st_mode,
            authority_info.st_nlink,
            authority_info.st_size,
            authority_info.st_mtime_ns,
            authority_info.st_ctime_ns,
        ) == authority_identity_before
        assert tree_signature(scenario.evidence_root) == evidence_before
        assert tree_signature(scenario.canonical_root) == canonical_before

        application_state = change_control_root / "state.sqlite3"
        shutil.copyfile(scenario.authority_path, application_state)
        application_state.chmod(0o600)
        application_before = application_state.read_bytes()
        generation_before = tree_signature(generation_root)
        query_workspace_before = tree_signature(query_workspace)
        settings = Settings.model_validate(
            {
                "storage": {"backend": "sqlite"},
                "embedding": {"provider": "mock"},
                "paths": {"workspace": query_workspace},
                "query_generation": {
                    "seed_repository_root": Path(__file__).resolve().parents[3],
                    "evidence_repository_root": scenario.evidence_root,
                    "canonical_repository_root": scenario.canonical_root,
                    "temporal_analysis_manifest_sha256": temporal_sha256,
                },
            }
        )
        with ChangeControlApplication(settings).resolve_query_generation(
            "active"
        ) as resolved:
            assert resolved.metadata.generation_kind == QueryGenerationKind.MANAGED
            assert resolved.metadata.generation_id == (
                resolved.metadata.active_generation_id
            )
            assert resolved.metadata.active_authority_revision == 1
            assert resolved.metadata.is_active
            assert resolved.metadata.index_file_sha256 is not None
            assert resolved.metadata.index_logical_fingerprint is not None
            assert int(resolved.backend.conn.execute("PRAGMA query_only").fetchone()[0]) == 1
            assert resolved.evidence_workspaces
            assert resolved.backend.conn.execute(
                "SELECT count(*) FROM documents"
            ).fetchone()[0] > 0
            query_embedder = MockEmbedding()
            approved_plan = next(
                item
                for item in scenario.subjects
                if isinstance(item, ManagedRevisionPlan)
                and any(
                    predecessor.statement != successor.statement
                    for predecessor, successor in zip(
                        item.predecessor_projection.projected_claims,
                        item.successor_projection.projected_claims,
                        strict=True,
                    )
                )
            )
            predecessor_statements = {
                item.statement
                for item in approved_plan.predecessor_projection.projected_claims
            }
            successor_statements = {
                item.statement for item in approved_plan.successor_projection.projected_claims
            }
            successor_only = successor_statements - predecessor_statements
            predecessor_only = predecessor_statements - successor_statements
            indexed_statements = {
                str(row[0])
                for row in resolved.backend.conn.execute("SELECT statement FROM claims")
            }
            assert successor_only
            assert successor_only <= indexed_statements
            assert predecessor_only.isdisjoint(indexed_statements)
            query = sorted(successor_only)[0]
            search = hybrid_search(
                query,
                settings,
                resolved.backend,
                query_embedder,
                evidence_workspaces=resolved.evidence_workspaces,
            )
            assert search.hits or search.wiki_card is not None
            answer = run_ask(
                query,
                settings,
                resolved.backend,
                query_embedder,
                MockLLM(),
                max_rounds=1,
                evidence_workspaces=resolved.evidence_workspaces,
                persist_run=False,
            )
            assert not answer.zero_evidence
            assert answer.evidence

        import mastervault.cli.ask as ask_cli_module
        import mastervault.cli.query as query_cli_module
        from mastervault.cli.app import app

        monkeypatch.setattr(query_cli_module, "load_settings", lambda: settings)
        monkeypatch.setattr(ask_cli_module, "load_settings", lambda: settings)
        runner = CliRunner()
        generation_id = activated.command.projection.generation_id

        searched = runner.invoke(app, ["search", query, "--json"])
        assert searched.exit_code == 0, searched.output
        search_payload = json.loads(searched.output)
        assert search_payload["generation"]["selection"]["selector"] == "auto"
        assert search_payload["generation"]["generation_id"] == generation_id
        assert search_payload["generation"]["is_active"] is True
        assert search_payload["hits"]

        exact = runner.invoke(
            app,
            ["search", query, "--generation", generation_id, "--json"],
        )
        assert exact.exit_code == 0, exact.output
        exact_payload = json.loads(exact.output)
        assert exact_payload["generation"]["generation_id"] == generation_id

        asked = runner.invoke(app, ["ask", query, "--max-rounds", "1", "--json"])
        assert asked.exit_code == 0, asked.output
        ask_payload = json.loads(asked.output)
        assert ask_payload["generation"]["generation_id"] == generation_id
        assert "knowledge generation:" in ask_payload["trace"]
        assert ask_payload["sources"]
        serialized_cli = json.dumps(
            {"search": search_payload, "exact": exact_payload, "ask": ask_payload},
            sort_keys=True,
        )
        for protected_root in (
            query_workspace,
            scenario.evidence_root,
            scenario.canonical_root,
            generation_root,
        ):
            assert str(protected_root) not in serialized_cli

        assert application_state.read_bytes() == application_before
        assert tree_signature(generation_root) == generation_before
        assert tree_signature(scenario.evidence_root) == evidence_before
        assert tree_signature(scenario.canonical_root) == canonical_before
        assert tree_signature(query_workspace) == query_workspace_before

        managed_index = (
            generation_root
            / "generations"
            / generation_id
            / "index"
            / "mastervault.sqlite3"
        )
        missing_index = managed_index.with_name("mastervault.sqlite3.missing")
        managed_index.replace(missing_index)
        try:
            missing = runner.invoke(app, ["search", query, "--json"])
            assert missing.exit_code == 1
            assert "integrity-failure" in missing.output
            assert "unmanaged" not in missing.output
        finally:
            missing_index.replace(managed_index)

        exact_index_bytes = managed_index.read_bytes()
        managed_index.chmod(0o600)
        with managed_index.open("r+b") as stream:
            stream.seek(100)
            byte = stream.read(1)
            stream.seek(100)
            stream.write(bytes([byte[0] ^ 1]))
            stream.flush()
            os.fsync(stream.fileno())
        managed_index.chmod(0o400)
        try:
            corrupt = runner.invoke(app, ["search", query, "--json"])
            assert corrupt.exit_code == 1
            assert "integrity-failure" in corrupt.output
            assert "unmanaged" not in corrupt.output
        finally:
            managed_index.chmod(0o600)
            managed_index.write_bytes(exact_index_bytes)
            managed_index.chmod(0o400)
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_b
def test_adoption_only_activation_records_no_fake_publication(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(
            scenario,
            prefix="generation:adoption-only",
            mode="adoption-only",
        )
        result = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:adoption-only:activate",
            generation_root=tmp_path / "generations",
        )

        assert result.receipt is not None
        assert result.receipt.publication_count == 0
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_revision_publication_events"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_d
def test_second_managed_successor_fails_before_repository_or_effects(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:single-successor", mode="mixed")
        first_root = tmp_path / "first-generations"
        first = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:single-successor:first",
            generation_root=first_root,
        )
        assert first.outcome == ManagedActivationOutcome.ACTIVATED
        effect_rows = scenario.store.conn.execute(
            "SELECT "
            "(SELECT count(*) FROM change_control_managed_activation_intents),"
            "(SELECT count(*) FROM change_control_revision_publication_events),"
            "(SELECT count(*) FROM change_control_index_generation_receipts),"
            "(SELECT count(*) FROM change_control_generation_activation_receipts)"
        ).fetchone()

        second_request_id = _decide(
            scenario,
            prefix="generation:single-successor:fresh-decision",
            mode="mixed",
        )
        second_root = tmp_path / "must-not-exist"
        with pytest.raises(ManagedActivationServiceError, match="one managed successor"):
            _activate(
                scenario,
                request_id=second_request_id,
                operation_id="generation:single-successor:second",
                generation_root=second_root,
            )

        assert not second_root.exists()
        assert (
            scenario.store.conn.execute(
                "SELECT "
                "(SELECT count(*) FROM change_control_managed_activation_intents),"
                "(SELECT count(*) FROM change_control_revision_publication_events),"
                "(SELECT count(*) FROM change_control_index_generation_receipts),"
                "(SELECT count(*) FROM change_control_generation_activation_receipts)"
            ).fetchone()
            == effect_rows
        )

        prior_state = scenario.store.get_managed_generation_activation(
            "generation:single-successor:first",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert prior_state is not None
        original = prior_state.intent.command
        active = scenario.store.get_active_generation(
            scenario.run_binding.prechange_head.aggregate_id,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        direct = ManagedActivationCommand.create(
            operation_id="generation:single-successor:direct-store-bypass",
            request_id=original.request_id,
            decision_id=original.decision_id,
            decision_record_sha256=original.decision_record_sha256,
            manifest_id=original.manifest_id,
            manifest_sha256=original.manifest_sha256,
            projection=original.projection,
            expected_authority=active,
            generation_repository_id=original.generation_repository_id,
            embedding_provider=original.embedding_provider,
            embedding_model_version=original.embedding_model_version,
            embedding_dimensions=original.embedding_dimensions,
        )
        with pytest.raises(ManagedGenerationActivationError, match="generation zero"):
            scenario.store.claim_managed_activation(
                direct,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_activation_intents"
            ).fetchone()[0]
            == int(effect_rows[0])
        )
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_c
def test_fully_rejected_decision_is_true_noop_without_repository_or_effect_rows(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:noop", mode="reject-all")
        generation_root = tmp_path / "never-created"
        result = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:noop:activate",
            generation_root=generation_root,
        )

        assert result.outcome == ManagedActivationOutcome.NO_OP
        assert result.command is None and result.receipt is None
        assert not generation_root.exists()
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_activation_intents"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_c
def test_unsupported_backend_fails_before_repository_or_effect_rows(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:postgres", mode="mixed")
        generation_root = tmp_path / "never-created"
        with pytest.raises(ManagedActivationBackendUnsupportedError, match="SQLite only"):
            activate_reviewed_managed_generation(
                request_id=request_id,
                operation_id="generation:postgres:activate",
                store=scenario.store,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                generation_root=generation_root,
                embedder=MockEmbedding(8),
                backend_kind="postgresql",
            )
        assert not generation_root.exists()
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_activation_intents"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_d
def test_change_control_authority_directory_is_protected_by_default(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:authority-root", mode="mixed")

        with pytest.raises(ManagedGenerationRepositoryError, match="disjoint"):
            _activate(
                scenario,
                request_id=request_id,
                operation_id="generation:authority-root:activate",
                generation_root=scenario.authority_path.parent,
            )

        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_activation_intents"
            ).fetchone()[0]
            == 0
        )
        assert not (scenario.authority_path.parent / ".evidence.lock").exists()
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_c
def test_publication_file_crash_reconciles_and_active_index_tamper_fails_closed(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        request_id = _decide(scenario, prefix="generation:recover", mode="mixed")
        generation_root = tmp_path / "generations"

        def fail_after_first_file(boundary: str) -> None:
            if boundary == "publication-file:0":
                raise RuntimeError("simulated crash after create-only publication")

        with pytest.raises(RuntimeError, match="simulated crash"):
            _activate(
                scenario,
                request_id=request_id,
                operation_id="generation:recover:activate",
                generation_root=generation_root,
                failure_hook=fail_after_first_file,
            )
        result = _activate(
            scenario,
            request_id=request_id,
            operation_id="generation:recover:activate",
            generation_root=generation_root,
        )
        assert result.command is not None and result.receipt is not None
        state = scenario.store.get_managed_generation_activation(
            "generation:recover:activate",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert state is not None and state.index_receipt is not None
        index_path = generation_root / state.index_receipt.index_relative_path
        assert index_path.stat().st_mode & 0o777 == 0o400
        index_path.chmod(0o600)
        with index_path.open("ab") as stream:
            stream.write(b"tamper")
        with pytest.raises(ManagedGenerationRepositoryError):
            _activate(
                scenario,
                request_id=request_id,
                operation_id="generation:recover:activate",
                generation_root=generation_root,
            )
        with pytest.raises(ManagedServingError):
            open_active_managed_sqlite_index(
                aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
                store=scenario.store,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                generation_root=generation_root,
            )
    finally:
        scenario.store.close()


@pytest.mark.parametrize(
    "boundary",
    (
        pytest.param(
            "intent-committed",
            marks=pytest.mark.generation_activation_d,
        ),
        pytest.param(
            "publication-receipt:0",
            marks=pytest.mark.generation_activation_d,
        ),
        pytest.param(
            "index-file-ready",
            marks=pytest.mark.generation_activation_b,
        ),
        pytest.param(
            "index-receipt-committed",
            marks=pytest.mark.generation_activation_c,
        ),
        pytest.param(
            "before-authority-cas",
            marks=pytest.mark.generation_activation_c,
        ),
        pytest.param(
            "authority-updated-before-receipt",
            marks=pytest.mark.generation_activation_b,
        ),
        pytest.param(
            "authority-cas-committed",
            marks=pytest.mark.generation_activation_a,
        ),
    ),
)
def test_every_durable_activation_boundary_reconciles_exactly(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    boundary: str,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        safe_boundary = boundary.replace(":", "-")
        prefix = f"generation:boundary:{safe_boundary}"
        request_id = _decide(scenario, prefix=prefix, mode="mixed")
        generation_root = tmp_path / "generations"

        def fail_once(observed: str) -> None:
            if observed == boundary:
                raise RuntimeError(f"simulated crash at {boundary}")

        with pytest.raises(RuntimeError, match="simulated crash"):
            _activate(
                scenario,
                request_id=request_id,
                operation_id=f"{prefix}:activate",
                generation_root=generation_root,
                failure_hook=fail_once,
            )
        if boundary == "authority-updated-before-receipt":
            assert (
                scenario.store.get_active_managed_generation_state(
                    scenario.run_binding.prechange_head.aggregate_id,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                )
                is None
            )
            assert (
                scenario.store.conn.execute(
                    "SELECT count(*) FROM change_control_generation_activation_receipts"
                ).fetchone()[0]
                == 0
            )
        scenario.store.close()
        scenario = replace(
            scenario,
            store=SqliteManagedChangeControlStore(
                scenario.authority_path,
                secure_open=True,
            ),
        )
        recovered = _activate(
            scenario,
            request_id=request_id,
            operation_id=f"{prefix}:activate",
            generation_root=generation_root,
        )
        replay = _activate(
            scenario,
            request_id=request_id,
            operation_id=f"{prefix}:activate",
            generation_root=generation_root,
        )

        assert recovered == replay
        assert recovered.receipt is not None
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_activation_receipts"
            ).fetchone()[0]
            == 1
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_index_generation_receipts"
            ).fetchone()[0]
            == 1
        )
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_a
def test_unsealed_index_crash_rebuilds_exact_rows_before_readiness(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        prefix = "generation:unsealed-index"
        request_id = _decide(scenario, prefix=prefix, mode="mixed")
        generation_root = tmp_path / "generations"
        def interrupt_readiness(
            repository: ManagedGenerationRepository,
            **kwargs: object,
        ) -> None:
            del repository, kwargs
            raise RuntimeError("simulated crash before readiness commit")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(
                ManagedGenerationRepository,
                "_create_index_readiness",
                interrupt_readiness,
            )
            with pytest.raises(RuntimeError, match="before readiness"):
                _activate(
                    scenario,
                    request_id=request_id,
                    operation_id=f"{prefix}:activate",
                    generation_root=generation_root,
                )

        state = scenario.store.get_managed_generation_activation(
            f"{prefix}:activate",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert state is not None and state.index_receipt is None
        index_path = generation_root / ManagedGenerationRepository.index_relative_path(
            generation_id=state.intent.command.projection.generation_id
        )
        assert index_path.stat().st_mode & 0o777 == 0o400
        index_path.chmod(0o600)
        backend = SqliteBackend(index_path)
        try:
            with backend.conn:
                target = str(
                    backend.conn.execute(
                        "SELECT doc_id FROM documents ORDER BY doc_id LIMIT 1"
                    ).fetchone()[0]
                )
                backend.conn.execute(
                    "UPDATE documents SET title='Malicious', body='Malicious body' WHERE doc_id=?",
                    (target,),
                )
                backend.conn.execute("DROP TABLE documents_fts")
                backend.conn.execute(
                    "CREATE TABLE documents_fts(doc_id TEXT PRIMARY KEY,title TEXT,body TEXT)"
                )
                backend.conn.execute(
                    "INSERT INTO documents_fts(doc_id,title,body) VALUES "
                    "(?,'Malicious','Malicious body')",
                    (target,),
                )
                backend.conn.execute("CREATE TABLE malicious_surplus(value TEXT)")
                vector_row = backend.conn.execute(
                    "SELECT record_id, embedding FROM vec_records ORDER BY record_id LIMIT 1"
                ).fetchone()
                assert vector_row is not None
                vector_record_id = str(vector_row[0])
                original_vector = bytes(vector_row[1])
                poisoned_vector = bytes(len(original_vector))
                assert original_vector != poisoned_vector
                backend.conn.execute(
                    "DELETE FROM vec_records WHERE record_id=?",
                    (vector_record_id,),
                )
                backend.conn.execute(
                    "INSERT INTO vec_records(record_id, embedding) VALUES (?, ?)",
                    (vector_record_id, poisoned_vector),
                )
        finally:
            backend.close()
        partial = index_path.read_bytes()[:127]
        assert partial
        index_path.write_bytes(partial)

        recovered = _activate(
            scenario,
            request_id=request_id,
            operation_id=f"{prefix}:activate",
            generation_root=generation_root,
        )
        assert recovered.receipt is not None
        assert index_path.stat().st_mode & 0o777 == 0o400
        serving = open_active_managed_sqlite_index(
            aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
            store=scenario.store,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            generation_root=generation_root,
        )
        try:
            assert (
                serving.conn.execute(
                    "SELECT count(*) FROM documents "
                    "WHERE title='Malicious' OR body='Malicious body'"
                ).fetchone()[0]
                == 0
            )
            rebuilt_vector = serving.conn.execute(
                "SELECT embedding FROM vec_records WHERE record_id=?",
                (vector_record_id,),
            ).fetchone()
            assert rebuilt_vector is not None
            assert bytes(rebuilt_vector[0]) == original_vector
            fts_schema = serving.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents_fts'"
            ).fetchone()
            assert fts_schema is not None
            assert "CREATE VIRTUAL TABLE" in str(fts_schema[0]).upper()
            assert (
                serving.conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name='malicious_surplus'"
                ).fetchone()[0]
                == 0
            )
        finally:
            serving.close()
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_d
def test_duplicate_fts_rows_cannot_be_sealed_and_retry_rebuilds_exactly(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        prefix = "generation:duplicate-fts"
        request_id = _decide(scenario, prefix=prefix, mode="mixed")
        operation_id = f"{prefix}:activate"
        generation_root = tmp_path / "generations"
        original_sync = generation_repository_module.sync_exact_vault_notes
        poisoned_counts: dict[str, tuple[int, int]] = {}

        def poison_fts(*args: object, **kwargs: object) -> object:
            report = original_sync(*args, **kwargs)
            backend = args[1]
            assert isinstance(backend, SqliteBackend)
            for table, select_sql, insert_sql, count_sql, empty_fallback in (
                (
                    "claims_fts",
                    "SELECT claim_id,statement FROM claims_fts ORDER BY rowid LIMIT 1",
                    "INSERT INTO claims_fts(claim_id,statement) VALUES (?,?)",
                    "SELECT COUNT(*) FROM claims_fts",
                    ("surplus-claim", "surplus claim"),
                ),
                (
                    "documents_fts",
                    "SELECT doc_id,title,body FROM documents_fts ORDER BY rowid LIMIT 1",
                    "INSERT INTO documents_fts(doc_id,title,body) VALUES (?,?,?)",
                    "SELECT COUNT(*) FROM documents_fts",
                    ("surplus-document", "Surplus document", "Surplus document body"),
                ),
                (
                    "structural_records_fts",
                    (
                        "SELECT record_id,text FROM structural_records_fts "
                        "ORDER BY rowid LIMIT 1"
                    ),
                    (
                        "INSERT INTO structural_records_fts(record_id,text) "
                        "VALUES (?,?)"
                    ),
                    "SELECT COUNT(*) FROM structural_records_fts",
                    ("surplus-structural-record", "surplus structural text"),
                ),
            ):
                row = backend.conn.execute(select_sql).fetchone()
                before = int(backend.conn.execute(count_sql).fetchone()[0])
                backend.conn.execute(
                    insert_sql,
                    empty_fallback if row is None else tuple(row),
                )
                after = int(backend.conn.execute(count_sql).fetchone()[0])
                assert after == before + 1
                poisoned_counts[table] = (before, after)
            return report

        with monkeypatch.context() as poisoned_sync:
            poisoned_sync.setattr(
                generation_repository_module,
                "sync_exact_vault_notes",
                poison_fts,
            )
            with pytest.raises(
                ManagedGenerationRepositoryError,
                match="managed claim FTS rows are not exact",
            ):
                _activate(
                    scenario,
                    request_id=request_id,
                    operation_id=operation_id,
                    generation_root=generation_root,
                )

        assert set(poisoned_counts) == {
            "claims_fts",
            "documents_fts",
            "structural_records_fts",
        }
        state = scenario.store.get_managed_generation_activation(
            operation_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert state is not None and state.index_receipt is None
        ready_path = generation_root / ManagedGenerationRepository.index_readiness_relative_path(
            generation_id=state.intent.command.projection.generation_id
        )
        assert not ready_path.exists()

        recovered = _activate(
            scenario,
            request_id=request_id,
            operation_id=operation_id,
            generation_root=generation_root,
        )
        assert recovered.receipt is not None
        index_state = scenario.store.get_managed_generation_activation(
            operation_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert index_state is not None and index_state.index_receipt is not None
        receipt_counts = dict(index_state.index_receipt.counts)
        assert ready_path.is_file()
        serving = open_active_managed_sqlite_index(
            aggregate_id=scenario.run_binding.prechange_head.aggregate_id,
            store=scenario.store,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            generation_root=generation_root,
        )
        try:
            for fts_table, fts_count_sql, source_count_sql, rowid_sql in (
                (
                    "claims_fts",
                    "SELECT COUNT(*) FROM claims_fts",
                    "SELECT COUNT(*) FROM claims",
                    "SELECT rowid FROM claims_fts ORDER BY rowid",
                ),
                (
                    "documents_fts",
                    "SELECT COUNT(*) FROM documents_fts",
                    "SELECT COUNT(*) FROM documents",
                    "SELECT rowid FROM documents_fts ORDER BY rowid",
                ),
                (
                    "structural_records_fts",
                    "SELECT COUNT(*) FROM structural_records_fts",
                    "SELECT COUNT(*) FROM structural_records",
                    "SELECT rowid FROM structural_records_fts ORDER BY rowid",
                ),
            ):
                fts_count = int(serving.conn.execute(fts_count_sql).fetchone()[0])
                source_count = int(serving.conn.execute(source_count_sql).fetchone()[0])
                rowids = [int(row[0]) for row in serving.conn.execute(rowid_sql).fetchall()]
                assert fts_count == source_count
                assert receipt_counts[fts_table] == source_count
                assert rowids == list(range(1, source_count + 1))
        finally:
            serving.close()
    finally:
        scenario.store.close()


@pytest.mark.generation_activation_b
def test_concurrent_activators_from_one_base_allow_exactly_one_successor(
    generation_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(generation_seed, tmp_path / "scenario")
    try:
        alternative_seed = build_real_managed_v2_variant(
            generation_seed, tmp_path / "alternative-seed"
        )
        alternative_seed.store.close()
        alternative = replace(
            alternative_seed,
            store=scenario.store,
            authority_path=scenario.authority_path,
        )
        mixed_request = _decide(
            scenario,
            prefix="generation:concurrent:mixed",
            mode="mixed",
        )
        adoption_request = _decide(
            alternative,
            prefix="generation:concurrent:adoption",
            mode="adoption-only",
        )

        class PreparationComplete(RuntimeError):
            pass

        def prepare(
            runtime: RealManagedV2Scenario,
            *,
            request_id: str,
            operation_id: str,
            generation_root: Path,
        ) -> tuple[ManagedActivationCommand, RepositoryVerifiedManagedGenerationEffects]:
            captured: list[
                tuple[ManagedActivationCommand, RepositoryVerifiedManagedGenerationEffects]
            ] = []

            def capture_before_cas(
                command: ManagedActivationCommand,
                *,
                capability: RepositoryVerifiedManagedGenerationEffects,
                **_kwargs: object,
            ) -> None:
                captured.append((command, capability))
                raise PreparationComplete

            with monkeypatch.context() as preparation:
                preparation.setattr(
                    runtime.store,
                    "activate_managed_generation",
                    capture_before_cas,
                )
                with pytest.raises(PreparationComplete):
                    _activate(
                        runtime,
                        request_id=request_id,
                        operation_id=operation_id,
                        generation_root=generation_root,
                    )

            assert len(captured) == 1
            command, capability = captured[0]
            state = runtime.store.get_managed_generation_activation(
                operation_id,
                resolver=runtime.resolver,
                verified_bootstrap=runtime.verified_bootstrap,
                prechange_head=runtime.prechange_head,
            )
            assert state is not None
            assert state.intent.command == command
            assert state.index_receipt is not None
            assert state.activation_receipt is None
            assert capability.publication_event_ids == tuple(
                event.event_id for event in state.publication_events
            )
            assert capability.publication_event_sha256s == tuple(
                event.event_sha256 for event in state.publication_events
            )
            assert capability.index_receipt_id == state.index_receipt.receipt_id
            assert capability.index_receipt_sha256 == state.index_receipt.receipt_sha256
            return command, capability

        mixed_command, mixed_capability = prepare(
            scenario,
            request_id=mixed_request,
            operation_id="generation:concurrent:mixed:activate",
            generation_root=tmp_path / "mixed-generations",
        )
        adoption_command, adoption_capability = prepare(
            alternative,
            request_id=adoption_request,
            operation_id="generation:concurrent:adoption:activate",
            generation_root=tmp_path / "adoption-generations",
        )

        barrier = Barrier(2)

        def attempt_activation(
            runtime: RealManagedV2Scenario,
            command: ManagedActivationCommand,
            capability: RepositoryVerifiedManagedGenerationEffects,
        ) -> None:
            store = SqliteManagedChangeControlStore(
                runtime.authority_path,
                secure_open=True,
            )
            try:
                store.activate_managed_generation(
                    command,
                    capability=capability,
                    resolver=runtime.resolver,
                    verified_bootstrap=runtime.verified_bootstrap,
                    prechange_head=runtime.prechange_head,
                )
            finally:
                store.close()

        def race_activation(
            runtime: RealManagedV2Scenario,
            command: ManagedActivationCommand,
            capability: RepositoryVerifiedManagedGenerationEffects,
        ) -> str:
            barrier.wait(timeout=10.0)
            try:
                attempt_activation(runtime, command, capability)
            except ManagedGenerationActivationStaleError:
                return "stale"
            except ChangeControlBusyError:
                return "busy"
            return "activated"

        candidates = (
            (scenario, mixed_command, mixed_capability),
            (alternative, adoption_command, adoption_capability),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(race_activation, *candidates[0]),
                pool.submit(race_activation, *candidates[1]),
            )
            _done, pending = wait(futures, timeout=120.0)
            assert not pending, "authority-only CAS race exceeded its bounded wait"
            outcomes = tuple(future.result() for future in futures)

        assert outcomes.count("activated") == 1
        assert (outcomes.count("stale"), outcomes.count("busy")) in {(1, 0), (0, 1)}
        if "busy" in outcomes:
            busy_index = outcomes.index("busy")
            with pytest.raises(ManagedGenerationActivationStaleError):
                attempt_activation(*candidates[busy_index])
            outcomes = tuple(
                "stale" if index == busy_index else outcome
                for index, outcome in enumerate(outcomes)
            )

        assert sorted(outcomes) == ["activated", "stale"]
        winner = candidates[outcomes.index("activated")]
        loser = candidates[outcomes.index("stale")]
        winner_runtime, winner_command, _ = winner
        loser_runtime, loser_command, _ = loser
        winner_state = scenario.store.get_managed_generation_activation(
            winner_command.operation_id,
            resolver=winner_runtime.resolver,
            verified_bootstrap=winner_runtime.verified_bootstrap,
            prechange_head=winner_runtime.prechange_head,
        )
        assert winner_state is not None and winner_state.activation_receipt is not None
        active = scenario.store.get_active_generation(
            winner_command.expected_authority.aggregate_id,
            verified_bootstrap=winner_runtime.verified_bootstrap,
            prechange_head=winner_runtime.prechange_head,
        )
        assert active == winner_state.activation_receipt.activated_authority
        assert active.active_generation.generation_id == winner_command.projection.generation_id

        loser_state = scenario.store.get_managed_generation_activation(
            loser_command.operation_id,
            resolver=loser_runtime.resolver,
            verified_bootstrap=loser_runtime.verified_bootstrap,
            prechange_head=loser_runtime.prechange_head,
        )
        assert loser_state is not None and loser_state.activation_receipt is None
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_activation_receipts"
            ).fetchone()[0]
            == 1
        )
        assert (
            scenario.store.conn.execute(
                "SELECT authority_revision FROM change_control_active_generation"
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(ChangeControlIdempotencyError, match="different inputs"):
            _activate(
                alternative,
                request_id=adoption_request,
                operation_id="generation:concurrent:mixed:activate",
                generation_root=tmp_path / "reused-operation-generations",
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_activation_intents"
            ).fetchone()[0]
            == 2
        )
    finally:
        scenario.store.close()
