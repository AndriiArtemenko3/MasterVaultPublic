"""Sidecar embedding importer + demo-dataset loader.

Two related fast paths that both avoid the multi-minute CPU embed pass over
the full Larkstead demo corpus (`mvault sync --full` with the local bge-small
ONNX provider runs roughly 16 minutes over 5k+ records):

- `load_embeddings` imports a precomputed `embeddings.jsonl.gz` sidecar
  (record_id, record_type, doc_id, domain, content_hash, model_version,
  vector) straight into a StorageBackend via batched `upsert_embeddings`
  calls. No embedding is computed here. A row is skipped rather than
  imported blind when its content_hash disagrees with the live vault text
  (stale sidecar) or its model_version does not match the active embedder.
- `load_demo_dataset` is the reusable core behind `mvault demo load`: copy
  the shipped `datasets/larkstead/processed/` domains plus its seeded review
  queue into a workspace, run a metadata-only sync (`embed=False`), then
  import the matching embeddings sidecar. Idempotent: safe to call again on
  an already-loaded workspace (existing domains are left alone unless
  `force=True`; the sync and embedding import are naturally idempotent).
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mastervault.providers import EmbeddingProvider
from mastervault.storage.base import EmbeddingRow, StorageBackend
from mastervault.sync.indexer import Progress, record_content_hashes, sync_vault

DEFAULT_BATCH_SIZE = 500
DOMAIN_DIRS = ("customer-support", "sales-crm", "operations", "internal-admin")


# ---------------------------------------------------------------------------
# Sidecar embedding import
# ---------------------------------------------------------------------------


@dataclass
class LoadEmbeddingsReport:
    total_rows: int = 0
    imported: int = 0
    skipped_model_mismatch: int = 0
    skipped_hash_mismatch: int = 0
    skipped_unknown_record: int = 0


def iter_embedding_rows(path: Path | str) -> Iterator[EmbeddingRow]:
    """Yield EmbeddingRow from a `.jsonl.gz` sidecar file, in file order."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            yield EmbeddingRow(
                record_id=data["record_id"],
                record_type=data["record_type"],
                doc_id=data.get("doc_id"),
                domain=data.get("domain"),
                content_hash=data["content_hash"],
                model_version=data["model_version"],
                vector=data["vector"],
            )


def load_embeddings(
    path: Path | str,
    backend: StorageBackend,
    *,
    expected_hashes: dict[str, str] | None = None,
    model_version: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> LoadEmbeddingsReport:
    """Batch-import a precomputed embeddings sidecar.

    `expected_hashes` (record_id -> current content_hash, typically from
    `mastervault.sync.record_content_hashes`) gates against importing a
    vector for text that no longer matches the live vault: a record_id
    absent from the map (record no longer exists) or whose hash disagrees is
    skipped rather than indexed. `model_version`, when given, skips rows
    built with a different embedding model than the active provider.
    """
    report = LoadEmbeddingsReport()
    batch: list[EmbeddingRow] = []

    def flush() -> None:
        if batch:
            backend.upsert_embeddings(list(batch))
            batch.clear()

    for row in iter_embedding_rows(path):
        report.total_rows += 1
        if model_version is not None and row.model_version != model_version:
            report.skipped_model_mismatch += 1
            continue
        if expected_hashes is not None:
            current = expected_hashes.get(row.record_id)
            if current is None:
                report.skipped_unknown_record += 1
                continue
            if current != row.content_hash:
                report.skipped_hash_mismatch += 1
                continue
        batch.append(row)
        report.imported += 1
        if len(batch) >= batch_size:
            flush()
    flush()
    return report


# ---------------------------------------------------------------------------
# Demo dataset loader
# ---------------------------------------------------------------------------


@dataclass
class DemoLoadReport:
    domains_copied: list[str] = field(default_factory=list)
    review_items_copied: int = 0
    sync_docs_upserted: int = 0
    sync_skipped: list[str] = field(default_factory=list)
    embeddings: LoadEmbeddingsReport = field(default_factory=LoadEmbeddingsReport)
    counts: dict[str, int] = field(default_factory=dict)
    wall_time_s: float = 0.0


def _copy_domains(processed_dir: Path, vault_dir: Path, *, force: bool) -> list[str]:
    vault_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for domain in DOMAIN_DIRS:
        src = processed_dir / domain
        if not src.is_dir():
            continue
        dest = vault_dir / domain
        if dest.exists():
            if not force:
                continue
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        copied.append(domain)
    return copied


def _copy_review_pending(processed_dir: Path, review_pending_dir: Path) -> int:
    review_pending_dir.mkdir(parents=True, exist_ok=True)
    src_dir = processed_dir / "_review" / "pending"
    if not src_dir.is_dir():
        return 0
    count = 0
    for item in sorted(src_dir.glob("*.md")):
        shutil.copy2(item, review_pending_dir / item.name)
        count += 1
    return count


def load_demo_dataset(
    *,
    dataset_dir: Path,
    vault_dir: Path,
    review_pending_dir: Path,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
    force: bool = False,
    progress: Progress | None = None,
) -> DemoLoadReport:
    """Copy the shipped demo dataset into a workspace and import its precomputed
    vectors: a metadata-only sync (no embed pass) plus a batched sidecar
    import, not a full re-embed of the corpus. Idempotent — call again on an
    already-loaded workspace to pick up sidecar/document changes cheaply.
    """

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    start = time.perf_counter()
    processed_dir = dataset_dir / "processed"
    embeddings_path = dataset_dir / "embeddings" / "embeddings.jsonl.gz"

    report = DemoLoadReport()
    report.domains_copied = _copy_domains(processed_dir, vault_dir, force=force)
    emit(f"copied domains: {report.domains_copied or '(already present; pass force=True to overwrite)'}")
    report.review_items_copied = _copy_review_pending(processed_dir, review_pending_dir)
    emit(f"copied {report.review_items_copied} pending review items")

    sync_report = sync_vault(vault_dir, backend, embedder, full=True, embed=False, progress=progress)
    report.sync_docs_upserted = sync_report.docs_upserted
    report.sync_skipped = [f"{s.rel_path}: {s.reason}" for s in sync_report.skipped]

    hashes = record_content_hashes(vault_dir)
    report.embeddings = load_embeddings(
        embeddings_path, backend, expected_hashes=hashes, model_version=embedder.model_version
    )
    emit(
        f"imported {report.embeddings.imported}/{report.embeddings.total_rows} embeddings "
        f"({report.embeddings.skipped_hash_mismatch} hash mismatch, "
        f"{report.embeddings.skipped_unknown_record} unknown record, "
        f"{report.embeddings.skipped_model_mismatch} model mismatch)"
    )

    stats = backend.stats()
    counts = dict(stats.get("counts", {}))
    counts["wiki"] = len({(d, s) for d, s in backend.alias_index().values()})
    counts["pending_review"] = (
        sum(1 for _ in review_pending_dir.glob("*.md")) if review_pending_dir.is_dir() else 0
    )
    report.counts = counts

    report.wall_time_s = round(time.perf_counter() - start, 3)
    emit(f"demo load finished in {report.wall_time_s}s")
    return report
