"""Sync layer: file-canonical vault -> derived storage index.

`sync_vault` is the main entry point. It walks the vault, upserts changed
documents (claims, chunks, aliases ride along), removes documents whose files
are gone, and (unless `embed=False`) runs the hash-gated embedding pass.
Deterministic and idempotent: a second run over an unchanged vault embeds
nothing. `mastervault.sync.load` layers a precomputed-vector fast path on top
for the shipped demo dataset.
"""

from mastervault.sync.indexer import (
    ExactVaultNoteInput,
    ExactWorkspaceFileInput,
    PreparedIndexDocument,
    PreparedIndexUnit,
    SyncReport,
    doc_id_for,
    prepare_exact_vault_notes,
    prepare_vault,
    record_content_hashes,
    sync_vault,
    wiki_definition_text,
)
from mastervault.sync.load import (
    DemoLoadReport,
    LoadEmbeddingsReport,
    load_demo_dataset,
    load_embeddings,
)

__all__ = [
    "DemoLoadReport",
    "ExactVaultNoteInput",
    "ExactWorkspaceFileInput",
    "LoadEmbeddingsReport",
    "PreparedIndexDocument",
    "PreparedIndexUnit",
    "SyncReport",
    "doc_id_for",
    "load_demo_dataset",
    "load_embeddings",
    "prepare_exact_vault_notes",
    "prepare_vault",
    "record_content_hashes",
    "sync_vault",
    "wiki_definition_text",
]
