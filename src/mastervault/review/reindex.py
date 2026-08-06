"""Synchronize and verify the derived-index side of an approved review change."""

from __future__ import annotations

from pathlib import Path

from mastervault.providers.embedding import EmbeddingProvider
from mastervault.storage.base import StorageBackend
from mastervault.sync import SyncReport, sync_vault


def sync_review_target(
    target: Path,
    *,
    vault_root: Path,
    backend: StorageBackend,
    embedder: EmbeddingProvider,
) -> SyncReport:
    """Sync the vault and fail unless the changed target was indexable."""
    # resolve_within(), used by review.apply, returns an absolute path even
    # when the configured workspace/vault path is relative.  Normalize both
    # sides before deriving the identity passed to the indexer.
    canonical_root = Path(vault_root).resolve()
    canonical_target = Path(target).resolve()
    try:
        target_rel = canonical_target.relative_to(canonical_root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            f"updated target is outside the configured vault: {canonical_target}"
        ) from exc

    report = sync_vault(canonical_root, backend, embedder)
    target_skip = next((skip for skip in report.skipped if skip.rel_path == target_rel), None)
    if target_skip is not None:
        raise RuntimeError(f"updated target was not indexable: {target_skip.reason}")
    if target_rel not in report.prepared_paths:
        raise RuntimeError(
            "updated target was not part of the prepared/indexed vault set "
            f"(unsupported extension, hidden path, or absent file): {target_rel}"
        )
    return report
