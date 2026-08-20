"""Focused complete-corpus contracts for workspace-origin generation one."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from mastervault.change_control import managed_generation_repository
from mastervault.change_control.generation_corpus import (
    GenerationCorpusIntegrityError,
    complete_generation_index_notes,
    verify_generation_base_inventory,
)
from mastervault.change_control.managed_generation import GenerationSourceNoteEntry
from mastervault.change_control.managed_generation_repository import (
    ManagedGenerationRepositoryError,
    ResolvedGenerationSourceNote,
)
from mastervault.change_control.managed_review import (
    GenerationZeroOriginBasis,
    WorkspaceGenerationZeroOriginBasis,
)
from mastervault.providers import MockEmbedding
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import (
    ExactVaultNoteInput,
    ExactWorkspaceFileInput,
    sync_exact_vault_notes,
)


@dataclass(frozen=True)
class _Note:
    entry: GenerationSourceNoteEntry
    content: bytes
    workspace: Path


def _command(*, workspace_origin: bool, entries: tuple[GenerationSourceNoteEntry, ...]):
    serving = tuple(item.entry_id for item in entries if item.included_in_serving_index)
    origin = (
        WorkspaceGenerationZeroOriginBasis.model_construct()
        if workspace_origin
        else GenerationZeroOriginBasis.model_construct()
    )
    return type(
        "Command",
        (),
        {
            "expected_authority": type("Authority", (), {"origin_basis": origin})(),
            "projection": type(
                "Projection",
                (),
                {"entries": entries, "serving_entry_ids": serving},
            )(),
        },
    )()


def _entry(path: str, marker: str, *, current: bool) -> GenerationSourceNoteEntry:
    return GenerationSourceNoteEntry.model_construct(
        entry_id=f"mgensource:{marker * 64}",
        logical_path=path,
        included_in_serving_index=current,
    )


def test_complete_workspace_corpus_keeps_nonmanaged_and_overlays_managed_path(
    tmp_path: Path,
) -> None:
    path = "support/sources/policy.md"
    predecessor = _entry(path, "1", current=False)
    successor = _entry(path, "2", current=True)
    base = (
        ExactVaultNoteInput("support/wiki/returns.md", b"wiki", tmp_path),
        ExactVaultNoteInput(path, b"old", tmp_path),
        ExactVaultNoteInput("ops/strategy/annual.md", b"strategy", tmp_path),
    )

    # Isolate overlay/accounting from frontmatter parsing; exact parsing is
    # covered at the repository build/verification boundary.
    with patch(
        "mastervault.change_control.generation_corpus.prepare_exact_vault_notes",
        side_effect=lambda notes: [
            type("Prepared", (), {"doc": type("Doc", (), {"rel_path": item.rel_path})()})()
            for item in notes
        ],
    ):
        result = complete_generation_index_notes(
            command=_command(workspace_origin=True, entries=(predecessor, successor)),
            managed_notes=(
                _Note(predecessor, b"old", tmp_path),
                _Note(successor, b"new", tmp_path / "generation"),
            ),
            base_notes=base,
        )

    assert tuple(item.rel_path for item in result) == (
        "ops/strategy/annual.md",
        path,
        "support/wiki/returns.md",
    )
    assert next(item for item in result if item.rel_path == path).content == b"new"


def test_generation_base_presence_must_match_origin(tmp_path: Path) -> None:
    current = _entry("support/sources/policy.md", "3", current=True)
    note = _Note(current, b"new", tmp_path)
    with pytest.raises(GenerationCorpusIntegrityError, match="authority origin"):
        complete_generation_index_notes(
            command=_command(workspace_origin=True, entries=(current,)),
            managed_notes=(note,),
            base_notes=None,
        )
    with pytest.raises(GenerationCorpusIntegrityError, match="authority origin"):
        complete_generation_index_notes(
            command=_command(workspace_origin=False, entries=(current,)),
            managed_notes=(note,),
            base_notes=(ExactVaultNoteInput(current.logical_path, b"old", tmp_path),),
        )


def test_workspace_base_rejects_a_duck_typed_capability(tmp_path: Path) -> None:
    command = _command(workspace_origin=True, entries=())

    with pytest.raises(GenerationCorpusIntegrityError, match="exact verified capability"):
        verify_generation_base_inventory(
            expected_authority=command.expected_authority,
            verified_workspace_bootstrap=object(),  # type: ignore[arg-type]
            base_notes=(ExactVaultNoteInput("support/wiki/returns.md", b"wiki", tmp_path),),
        )


def test_corpus_boundaries_snapshot_only_exact_value_carriers(tmp_path: Path) -> None:
    class SubstitutedBaseNote(ExactVaultNoteInput):
        pass

    current = _entry("support/sources/policy.md", "7", current=True)
    with pytest.raises(GenerationCorpusIntegrityError, match="substituted note"):
        complete_generation_index_notes(
            command=_command(workspace_origin=True, entries=(current,)),
            managed_notes=(_Note(current, b"new", tmp_path),),
            base_notes=(
                SubstitutedBaseNote(current.logical_path, b"old", tmp_path),
            ),
        )

    class SubstitutedManagedNote(ResolvedGenerationSourceNote):
        pass

    with pytest.raises(ManagedGenerationRepositoryError, match="substituted SourceNote"):
        managed_generation_repository._canonical_resolved_generation_notes(  # noqa: SLF001
            (
                SubstitutedManagedNote(
                    entry=current,
                    content=b"new",
                    workspace=tmp_path,
                ),
            )
        )


def test_complete_workspace_corpus_real_parse_retains_every_note_kind(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "mini_vault"
    paths = (
        "customer-support/wiki/refund-window.md",
        "customer-support/decisions/2026-03-02-extend-refund-window.md",
        "sales-crm/strategy/2026-q2.md",
        "customer-support/sources/faq-desk-mat-care.md",
        "customer-support/sources/policy-returns-and-refunds.md",
    )
    base = tuple(
        ExactVaultNoteInput(path, (fixture / path).read_bytes(), fixture)
        for path in sorted(paths)
    )
    managed_path = "customer-support/sources/policy-returns-and-refunds.md"
    predecessor = _entry(managed_path, "4", current=False)
    successor = _entry(managed_path, "5", current=True)
    successor_bytes = (fixture / managed_path).read_bytes().replace(
        b"The restocking fee is 10 percent",
        b"The restocking fee is 12 percent",
    )

    result = complete_generation_index_notes(
        command=_command(workspace_origin=True, entries=(predecessor, successor)),
        managed_notes=(
            _Note(predecessor, (fixture / managed_path).read_bytes(), fixture),
            _Note(successor, successor_bytes, tmp_path / "generation"),
        ),
        base_notes=base,
    )

    by_path = {item.rel_path: item for item in result}
    assert set(by_path) == set(paths)
    assert by_path[managed_path].content == successor_bytes
    assert (fixture / managed_path).read_bytes() not in {
        item.content for item in result if item.rel_path == managed_path
    }
    assert {path.split("/")[-2] for path in by_path} >= {
        "wiki",
        "decisions",
        "strategy",
        "sources",
    }

    backend = SqliteBackend(":memory:")
    embedder = MockEmbedding()
    try:
        backend.init_schema(embedder.dimensions, embedder.model_version)
        report = sync_exact_vault_notes(result, backend, embedder, force_embeddings=True)
        indexed = backend.conn.execute(
            "SELECT rel_path, doc_type, doc_id FROM documents ORDER BY rel_path"
        ).fetchall()
        managed_doc_id = next(
            str(row[2]) for row in indexed if str(row[0]) == managed_path
        )
        managed_claims = {
            str(row[0])
            for row in backend.conn.execute(
                "SELECT statement FROM claims WHERE doc_id=?",
                (managed_doc_id,),
            )
        }
    finally:
        backend.close()

    assert report.docs_upserted == len(paths)
    assert {str(row[0]) for row in indexed} == set(paths)
    assert {str(row[1]) for row in indexed} >= {
        "wiki",
        "decision",
        "strategy",
        "source",
    }
    assert any("12 percent" in statement for statement in managed_claims)
    assert not any("10 percent" in statement for statement in managed_claims)


def test_pdf_support_is_carried_only_when_successor_bindings_still_match(
    tmp_path: Path,
) -> None:
    path = "support/sources/pdf-policy.md"
    current = _entry(path, "6", current=True)
    support = (
        # The parser is patched because this test isolates that the exact base
        # support tuple is selected and supplied to validation for the overlay.
        ExactWorkspaceFileInput(rel_path="assets/a.pdf", content=b"pdf"),
        ExactWorkspaceFileInput(rel_path="parsed/a.json", content=b"json"),
    )
    base_note = ExactVaultNoteInput(
        path,
        b"old",
        tmp_path / "base",
        supporting_files=support,
    )
    managed = _Note(current, b"new", tmp_path / "generation")

    with patch(
        "mastervault.change_control.generation_corpus.prepare_exact_vault_notes",
        side_effect=ValueError("stale parsed-document binding"),
    ), pytest.raises(
        GenerationCorpusIntegrityError,
        match="structural bindings cannot be carried",
    ):
        complete_generation_index_notes(
            command=_command(workspace_origin=True, entries=(current,)),
            managed_notes=(managed,),
            base_notes=(base_note,),
        )
