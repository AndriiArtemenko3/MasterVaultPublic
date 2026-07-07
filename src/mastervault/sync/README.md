# src/mastervault/sync — Vault tree into the storage index

This folder reconciles the file-canonical vault against the derived storage index. It walks the notes, upserts documents whose content changed (claims, chunks, and wiki aliases ride along), deletes rows for files that are gone, and runs a hash-gated embedding pass so a re-run over an unchanged vault embeds nothing. A second module adds a precomputed-vector fast path so the shipped demo dataset loads in seconds instead of the ~16 minutes a full CPU embed of 5k+ records would take.

## Files

| File | Responsibility |
|------|----------------|
| `indexer.py` | `sync_vault` and its helpers. Two-layer change detection: a full-file content hash decides per-document re-upsert, and a per-record content hash plus `backend.needs_embedding` gates each paid embed call. `prepare_vault` turns walked notes into document/claim/chunk/alias rows and embeddable `_Unit`s without touching the backend; `record_content_hashes` exposes the current `{record_id: content_hash}` map for sidecar validation. Delete reconciliation removes any indexed document whose `rel_path` is no longer present. |
| `load.py` | Sidecar embedding import + demo loader. `load_embeddings` batch-imports a precomputed `embeddings.jsonl.gz` file via `upsert_embeddings`, skipping rows whose `content_hash` disagrees with the live vault or whose `model_version` does not match the active embedder. `load_demo_dataset` copies the shipped `datasets/larkstead/processed/` domains and its seeded review queue into a workspace, runs a metadata-only sync (`embed=False`), then imports the matching sidecar. |
| `__init__.py` | Public surface for the sync layer: re-exports `sync_vault`, `prepare_vault`, `record_content_hashes`, `doc_id_for`, `wiki_definition_text`, and the `load_*` functions with their report dataclasses. |

## How it fits

Input comes from [../vaultfs](../vaultfs): `walk_vault` enumerates note refs, `read_note` parses frontmatter into typed models, and `segment` produces body chunks. `sync_vault` writes the results through a [../storage](../storage) `StorageBackend` (SQLite or Postgres) and calls a [../providers](../providers) `EmbeddingProvider` for the vectors. The rows and embeddings it lands are what [../retrieval](../retrieval) later reads across its lexical, vector, and alias-graph channels.

## Key concepts / entry points

- `sync_vault(vault_dir, backend, embedder, *, full, embed, progress)` — the layer's main entry point: walk, upsert changed docs, delete absent docs, hash-gated embed pass (`indexer.py:225`).
- `prepare_vault(vault_dir)` — walk + build rows and `_Unit`s with no backend or embedder side effects; shared by the sync and the sidecar importer (`indexer.py:180`).
- `record_content_hashes(vault_dir)` — `{record_id: content_hash}` for the vault's current embeddable units, used to reject stale sidecar vectors before import (`indexer.py:210`).
- Two-layer change detection — document re-upsert keys on the whole-file `content_hash`; embedding keys on the per-text `content_hash` via `backend.needs_embedding`, so `full=True` can re-upsert every document yet still embed nothing (`indexer.py:256`).
- `load_embeddings(path, backend, *, expected_hashes, model_version, batch_size)` — batched sidecar import with hash and model-version gates and per-reason skip counts (`load.py:72`).
- `load_demo_dataset(...)` — copy domains + review queue, metadata-only sync, sidecar import; idempotent and timed (`load.py:163`).
