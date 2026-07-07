"""Sync layer: file-canonical vault -> derived storage index.

`sync_vault` is the only entry point. It walks the vault, upserts changed
documents (claims, chunks, aliases ride along), removes documents whose files
are gone, and runs the hash-gated embedding pass. Deterministic and
idempotent: a second run over an unchanged vault embeds nothing.
"""

from mastervault.sync.indexer import SyncReport, doc_id_for, sync_vault, wiki_definition_text

__all__ = ["SyncReport", "doc_id_for", "sync_vault", "wiki_definition_text"]
