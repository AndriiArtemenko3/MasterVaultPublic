"""Focused tests for the shared managed-generation resolution boundary."""

from __future__ import annotations

import ast
import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mastervault.change_control import generation_resolution as resolution_module
from mastervault.change_control.generation_resolution import (
    derive_generation_projection,
    resolve_generation_notes,
)
from mastervault.change_control.managed_activation_service import (
    ManagedActivationServiceError,
)
from mastervault.change_control.managed_generation import (
    GenerationSourceNoteEntry,
    PublishedSourceBinding,
    ReviewedSourceBinding,
)
from mastervault.change_control.managed_generation_repository import (
    ResolvedGenerationSourceNote,
)
from mastervault.change_control.models import (
    DocumentAuthority,
    DocumentRole,
    DocumentVersionMetadata,
    TemporalState,
)


def _document() -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id="returns-faq",
        document_family="returns-faq",
        version_label="v1",
        source_path="raw/returns-faq.md",
        source_sha256="1" * 64,
        declared_effective_from=date(2026, 1, 1),
        role=DocumentRole.FAQ,
        authority=DocumentAuthority.DELEGATED,
    )


def _entry(*, source: ReviewedSourceBinding | PublishedSourceBinding, content: bytes):
    return GenerationSourceNoteEntry.create(
        logical_path="customer-support/sources/returns-faq.md",
        document=_document(),
        source_note_sha256=hashlib.sha256(content).hexdigest(),
        source_note_byte_count=len(content),
        temporal_state=TemporalState.CURRENT,
        included_in_serving_index=True,
        source=source,
    )


def test_derive_generation_projection_forwards_exact_reviewed_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = object()
    inventory = object()
    constraints = object()
    expected = object()
    source = SimpleNamespace(
        inventory=inventory,
        snapshot=SimpleNamespace(
            aggregate=SimpleNamespace(validated_temporal_constraints=lambda: constraints)
        ),
    )
    captured: dict[str, object] = {}

    def derive(**kwargs: object) -> object:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(resolution_module, "derive_managed_generation_projection", derive)

    assert derive_generation_projection(decision=decision, source=source) is expected
    assert captured == {
        "decision": decision,
        "reviewed_inventory": inventory,
        "temporal_constraints": constraints,
    }


def test_resolve_generation_notes_preserves_reviewed_source_bytes(tmp_path: Path) -> None:
    content = b"---\ntype: source\n---\n\nReviewed bytes.\n"
    snapshot_sha = "2" * 64
    binding = ReviewedSourceBinding(
        reviewed_inventory_sha256="3" * 64,
        source_note_snapshot_id=f"depsource:{snapshot_sha}",
        source_note_snapshot_sha256=snapshot_sha,
    )
    entry = _entry(source=binding, content=content)
    workspace = tmp_path / "reviewed-workspace"
    note = SimpleNamespace(
        document=entry.document,
        source_note_path=entry.logical_path,
        source_note_sha256=entry.source_note_sha256,
        source_note_utf8_bytes=entry.source_note_byte_count,
        snapshot_id=binding.source_note_snapshot_id,
        snapshot_sha256=binding.source_note_snapshot_sha256,
        source_note_utf8=content.decode("utf-8"),
    )
    source = SimpleNamespace(
        inventory=SimpleNamespace(notes=(note,)),
        workspace_root=workspace,
    )
    projection = SimpleNamespace(
        generation_id="mgeneration:" + "4" * 64,
        entries=(entry,),
    )
    state = SimpleNamespace(publication_events=())
    repository = SimpleNamespace(root=tmp_path / "generation-repository")

    assert resolve_generation_notes(
        source=source,
        projection=projection,
        state=state,
        repository=repository,
    ) == (ResolvedGenerationSourceNote(entry=entry, content=content, workspace=workspace),)


def test_resolve_generation_notes_preserves_published_source_bytes(tmp_path: Path) -> None:
    content = b"---\ntype: source\n---\n\nPublished bytes.\n"
    binding = PublishedSourceBinding(
        target_key="returns-faq",
        predecessor_document_version_id=_document().document_version_id,
        staged_artifact_id="martifact:" + "5" * 64,
        destination_id="mdestination:" + "6" * 64,
        destination_path="customer-support/sources/returns-faq.md",
    )
    entry = _entry(source=binding, content=content)
    event = SimpleNamespace(
        publication=SimpleNamespace(
            staged_artifact=SimpleNamespace(artifact_id=binding.staged_artifact_id),
            destination=SimpleNamespace(
                destination_id=binding.destination_id,
                path=binding.destination_path,
            ),
        )
    )

    class Repository:
        root = tmp_path / "generation-repository"

        @staticmethod
        def open_publication(value: Any) -> bytes:
            assert value is event
            return content

    projection = SimpleNamespace(
        generation_id="mgeneration:" + "7" * 64,
        entries=(entry,),
    )
    source = SimpleNamespace(
        inventory=SimpleNamespace(notes=()),
        workspace_root=tmp_path / "reviewed-workspace",
    )

    resolved = resolve_generation_notes(
        source=source,
        projection=projection,
        state=SimpleNamespace(publication_events=(event,)),
        repository=Repository(),
    )

    assert resolved == (
        ResolvedGenerationSourceNote(
            entry=entry,
            content=content,
            workspace=Repository.root
            / "generations"
            / projection.generation_id
            / "canonical",
        ),
    )


def test_resolve_generation_notes_preserves_duplicate_destination_failure(
    tmp_path: Path,
) -> None:
    event = SimpleNamespace(
        publication=SimpleNamespace(destination=SimpleNamespace(destination_id="same"))
    )

    with pytest.raises(
        ManagedActivationServiceError,
        match="managed publication events contain duplicate destinations",
    ):
        resolve_generation_notes(
            source=SimpleNamespace(inventory=SimpleNamespace(notes=())),
            projection=SimpleNamespace(generation_id="mgeneration:" + "8" * 64, entries=()),
            state=SimpleNamespace(publication_events=(event, event)),
            repository=SimpleNamespace(root=tmp_path),
        )


def test_activation_and_serving_do_not_import_sibling_private_helpers() -> None:
    source_root = Path(__file__).parents[3] / "src" / "mastervault" / "change_control"
    private_imports: list[tuple[str, str, str]] = []
    for name in ("managed_activation_service.py", "managed_serving.py"):
        tree = ast.parse((source_root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "mastervault.change_control."
            ):
                continue
            private_imports.extend(
                (name, node.module or "", imported.name)
                for imported in node.names
                if imported.name.startswith("_")
            )

    assert private_imports == []
