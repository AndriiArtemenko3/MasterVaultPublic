from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from mastervault.contracts.page_grounded_claims import EvidenceCandidate
from mastervault.core.errors import DocumentIntegrityError
from mastervault.document_intelligence import (
    load_pdf_source,
    parse_pdf,
    resolve_evidence,
    store_parsed_document,
    store_source_asset,
)
from mastervault.models import Claim, SourceNote
from mastervault.sync.indexer import (
    ExactSourceNoteInput,
    ExactVaultNoteInput,
    ExactWorkspaceFileInput,
    prepare_exact_source_notes,
    prepare_exact_vault_notes,
    prepare_vault,
)
from mastervault.vaultfs.notes import write_note

MINI_VAULT = Path(__file__).parents[1] / "fixtures" / "mini_vault"
PDF_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf"
)
PDF_QUOTE = "Customers may return any item within 45 days of the delivery date."


def _inventory() -> tuple[ExactVaultNoteInput, ...]:
    return tuple(
        ExactVaultNoteInput(
            rel_path=path.relative_to(MINI_VAULT).as_posix(),
            content=path.read_bytes(),
            workspace=MINI_VAULT.parent,
        )
        for path in sorted(MINI_VAULT.rglob("*.md"))
    )


def test_exact_vault_projection_matches_normal_complete_walk() -> None:
    expected, skipped = prepare_vault(MINI_VAULT)
    exact = prepare_exact_vault_notes(_inventory())

    assert skipped == []
    assert exact == expected


def test_exact_vault_projection_has_no_skip_or_reordering_channel() -> None:
    inventory = _inventory()

    with pytest.raises(ValueError, match="canonical path order"):
        prepare_exact_vault_notes(tuple(reversed(inventory)))

    non_source = next(item for item in inventory if "/wiki/" in item.rel_path)
    with pytest.raises(ValueError, match="SourceNotes only"):
        prepare_exact_source_notes(
            (
                ExactSourceNoteInput(
                    rel_path=non_source.rel_path,
                    content=non_source.content,
                    workspace=non_source.workspace,
                ),
            )
        )


@pytest.mark.parametrize("tampered_support", ["asset", "parsed"])
def test_exact_pdf_projection_binds_both_supporting_files_and_rejects_tampering(
    tmp_path: Path,
    tampered_support: str,
) -> None:
    workspace = tmp_path / "workspace"
    document = parse_pdf(PDF_FIXTURE)
    asset = store_source_asset(load_pdf_source(PDF_FIXTURE), workspace)
    parsed = store_parsed_document(document, workspace)
    evidence = resolve_evidence(
        document,
        [
            EvidenceCandidate(
                block_id="page-0001-block-0001",
                quote=PDF_QUOTE,
            )
        ],
    )
    note = SourceNote(
        domain="customer-support",
        title="Exact PDF projection",
        created=date(2026, 8, 12),
        updated=date(2026, 8, 12),
        source_type="policy",
        provenance=asset.stored_path,
        key_claims=[
            Claim(
                id="exact-pdf-projection-01",
                statement=PDF_QUOTE,
                confidence="high",
                evidence=evidence,
            )
        ],
        source_asset=asset,
        parsed_document=parsed,
    )
    rel_path = "customer-support/sources/exact-pdf-projection.md"
    note_path = workspace / rel_path
    write_note(note_path, note, "# Exact PDF projection")
    support = (
        ExactWorkspaceFileInput(
            rel_path=asset.stored_path,
            content=(workspace / asset.stored_path).read_bytes(),
        ),
        ExactWorkspaceFileInput(
            rel_path=parsed.artifact_path,
            content=(workspace / parsed.artifact_path).read_bytes(),
        ),
    )
    exact = ExactVaultNoteInput(
        rel_path=rel_path,
        content=note_path.read_bytes(),
        workspace=workspace,
        supporting_files=support,
    )

    prepared = prepare_exact_vault_notes((exact,))
    assert len(prepared) == 1
    assert prepared[0].doc.rel_path == rel_path

    tampered_index = 0 if tampered_support == "asset" else 1
    tampered = list(support)
    tampered[tampered_index] = replace(
        tampered[tampered_index],
        content=b"tampered exact supporting bytes",
    )
    with pytest.raises(DocumentIntegrityError, match="integrity mismatch|hash mismatch"):
        prepare_exact_vault_notes((replace(exact, supporting_files=tuple(tampered)),))
