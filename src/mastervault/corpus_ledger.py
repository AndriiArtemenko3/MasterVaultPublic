"""Deterministic raw-to-processed corpus accounting for shipped datasets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mastervault.models import content_hash
from mastervault.vaultfs.frontmatter import parse_frontmatter

LEDGER_SCHEMA_VERSION = 1
HISTORY_SCHEMA_VERSION = 1
EXCLUSION_SCHEMA_VERSION = 1
HISTORY_STAGE = "historical_snapshot_comparison"
HISTORY_REASON_CODE = "historical_no_output_unknown_cause"
HISTORY_MESSAGE = (
    "no processed output names this raw source in the referenced 2026-07-07 snapshot; "
    "whether the source failed, was skipped, or stopped at another stage is unknown"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _repo_path(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _processed_by_provenance(repo_root: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    processed_root = repo_root / "datasets" / "larkstead" / "processed"
    found: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for path in sorted(processed_root.glob("*/sources/*.md")):
        try:
            data, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - dataset gate reports every malformed file
            errors.append(f"{_repo_path(repo_root, path)}: invalid frontmatter: {exc}")
            continue
        provenance = data.get("provenance")
        provenance_hash = data.get("provenance_hash")
        if not isinstance(provenance, str) or not provenance:
            errors.append(f"{_repo_path(repo_root, path)}: missing provenance")
            continue
        if not isinstance(provenance_hash, str) or not provenance_hash:
            errors.append(f"{_repo_path(repo_root, path)}: missing provenance_hash")
            continue
        if provenance in found:
            errors.append(
                f"{provenance}: multiple processed notes: "
                f"{found[provenance]['processed_path']} and {_repo_path(repo_root, path)}"
            )
            continue
        found[provenance] = {
            "processed_path": _repo_path(repo_root, path),
            "processed_sha256": _sha256(path),
            "provenance_hash": provenance_hash,
        }
    return found, errors


def load_history_record(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: history/exclusion record must be a JSON object")
    return value


def build_ledger(
    repo_root: Path,
    history_record_path: Path,
    exclusion_record_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the expected ledger from raw files, provenance, exclusions, and history."""
    repo_root = repo_root.resolve()
    exclusion_record_path = exclusion_record_path or (
        repo_root / "datasets" / "larkstead" / "exclusions.json"
    )
    processed, errors = _processed_by_provenance(repo_root)
    history_record = load_history_record(history_record_path)
    if history_record.get("schema_version") != HISTORY_SCHEMA_VERSION:
        errors.append(
            f"{_repo_path(repo_root, history_record_path)}: unsupported history schema_version"
        )

    history: dict[str, dict[str, Any]] = {}
    history_rows = history_record.get("observations", [])
    if not isinstance(history_rows, list):
        errors.append("history record 'observations' must be a list")
        history_rows = []
    history_ids: set[str] = set()
    for row in history_rows:
        if not isinstance(row, dict) or not isinstance(row.get("raw_path"), str):
            errors.append("history record contains an invalid observation")
            continue
        raw_path = row["raw_path"]
        if raw_path in history:
            errors.append(f"{raw_path}: duplicate historical observation")
        history_id = row.get("id")
        if not isinstance(history_id, str) or not history_id:
            errors.append(f"{raw_path}: historical observation needs a stable id")
        elif history_id in history_ids:
            errors.append(f"{raw_path}: duplicate historical observation id {history_id!r}")
        else:
            history_ids.add(history_id)
        if row.get("stage") != HISTORY_STAGE:
            errors.append(f"{raw_path}: unsupported history stage {row.get('stage')!r}")
        if row.get("reason_code") != HISTORY_REASON_CODE:
            errors.append(f"{raw_path}: unexpected history reason_code")
        if row.get("observation") != HISTORY_MESSAGE:
            errors.append(f"{raw_path}: unexpected historical observation text")
        if row.get("attempts") is not None:
            errors.append(f"{raw_path}: historical attempt count must remain explicitly unknown")
        history[raw_path] = row

    exclusion_record = load_history_record(exclusion_record_path)
    if exclusion_record.get("schema_version") != EXCLUSION_SCHEMA_VERSION:
        errors.append(
            f"{_repo_path(repo_root, exclusion_record_path)}: unsupported exclusion schema_version"
        )
    exclusions: dict[str, dict[str, Any]] = {}
    exclusion_rows = exclusion_record.get("exclusions", [])
    if not isinstance(exclusion_rows, list):
        errors.append("exclusion record 'exclusions' must be a list")
        exclusion_rows = []
    for row in exclusion_rows:
        if not isinstance(row, dict) or not isinstance(row.get("raw_path"), str):
            errors.append("exclusion record contains an invalid row")
            continue
        raw_path = row["raw_path"]
        if raw_path in exclusions:
            errors.append(f"{raw_path}: duplicate exclusion record")
        reason_code = row.get("reason_code")
        reason = row.get("reason")
        if not isinstance(reason_code, str) or not reason_code.strip():
            errors.append(f"{raw_path}: exclusion needs a stable reason_code")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{raw_path}: exclusion needs a truthful reason")
        exclusions[raw_path] = row

    raw_root = repo_root / "datasets" / "larkstead" / "raw"
    entries: list[dict[str, Any]] = []
    raw_paths = [_repo_path(repo_root, path) for path in sorted(raw_root.rglob("*.md"))]
    for raw_path in raw_paths:
        path = repo_root / raw_path
        raw_sha256 = _sha256(path)
        short_hash = content_hash(path.read_text(encoding="utf-8"))
        processed_row = processed.get(raw_path)
        history_row = history.get(raw_path)
        excluded_row = exclusions.get(raw_path)
        # Historical observations are immutable event history, not a second
        # current classification. A source can gain a processed output later
        # while retaining the fact that it had no output in the old snapshot.
        classifications = sum(row is not None for row in (processed_row, excluded_row))
        if classifications > 1:
            errors.append(f"{raw_path}: has multiple processed/excluded classifications")
            continue
        if processed_row is not None:
            if processed_row["provenance_hash"] != short_hash:
                errors.append(
                    f"{raw_path}: provenance_hash mismatch "
                    f"({processed_row['provenance_hash']} != {short_hash})"
                )
            entry: dict[str, Any] = {
                "raw_path": raw_path,
                "raw_sha256": raw_sha256,
                "status": "processed",
                **processed_row,
            }
            if history_row is not None:
                entry["historical_observation"] = {
                    "record": _repo_path(repo_root, history_record_path),
                    "id": history_row.get("id"),
                    "reason_code": history_row.get("reason_code"),
                }
            entries.append(entry)
            continue
        if history_row is not None:
            if history_row.get("raw_sha256") != raw_sha256:
                errors.append(f"{raw_path}: historical record raw_sha256 mismatch")
            entries.append(
                {
                    "raw_path": raw_path,
                    "raw_sha256": raw_sha256,
                    "status": "historical_no_output",
                    "reason_code": history_row.get("reason_code"),
                    "historical_record": _repo_path(repo_root, history_record_path),
                    "historical_observation_id": history_row.get("id"),
                }
            )
            continue
        if excluded_row is not None:
            if excluded_row.get("raw_sha256") != raw_sha256:
                errors.append(f"{raw_path}: exclusion record raw_sha256 mismatch")
            entries.append(
                {
                    "raw_path": raw_path,
                    "raw_sha256": raw_sha256,
                    "status": "excluded",
                    "reason_code": excluded_row.get("reason_code"),
                    "reason": excluded_row.get("reason"),
                    "exclusion_record": _repo_path(repo_root, exclusion_record_path),
                }
            )
            continue
        errors.append(
            f"{raw_path}: has no processed, excluded, or historical_no_output classification"
        )

    raw_set = set(raw_paths)
    for extra in sorted(set(processed) - raw_set):
        errors.append(f"{extra}: processed provenance does not resolve to a raw Markdown file")
    for extra in sorted(set(history) - raw_set):
        errors.append(f"{extra}: historical observation does not resolve to a raw Markdown file")
    for extra in sorted(set(exclusions) - raw_set):
        errors.append(f"{extra}: exclusion record does not resolve to a raw Markdown file")

    summary = {
        "raw": len(raw_paths),
        "processed": sum(row["status"] == "processed" for row in entries),
        "excluded": sum(row["status"] == "excluded" for row in entries),
        "historical_no_output": sum(
            row["status"] == "historical_no_output" for row in entries
        ),
        "historical_no_output_observations": len(history),
    }
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "dataset": "larkstead",
        "hash_algorithm": "sha256",
        "summary": summary,
        "entries_sha256": _canonical_hash(entries),
        "entries": entries,
    }
    return ledger, errors


def validate_committed_ledger(
    repo_root: Path,
    ledger_path: Path,
    history_record_path: Path,
    exclusion_record_path: Path | None = None,
) -> list[str]:
    expected, errors = build_ledger(repo_root, history_record_path, exclusion_record_path)
    try:
        committed = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [*errors, f"{ledger_path}: cannot read committed ledger: {exc}"]
    if committed != expected:
        errors.append(
            f"{ledger_path}: ledger is stale; run "
            "`uv run python datasets/larkstead/qa/validate_corpus_ledger.py --write`"
        )
    return errors


def verify_historical_no_output(repo_root: Path, history_record_path: Path) -> list[str]:
    """Verify immutable raw hashes retained with the historical observations.

    This intentionally does not infer or reproduce a failure mechanism, and a
    later processed result does not invalidate the earlier snapshot fact.
    """
    record = load_history_record(history_record_path)
    errors: list[str] = []
    if record.get("schema_version") != HISTORY_SCHEMA_VERSION:
        errors.append("unsupported history schema_version")
    for row in record.get("observations", []):
        raw_path = repo_root / row["raw_path"]
        if not raw_path.is_file():
            errors.append(f"{row['raw_path']}: raw source is missing")
            continue
        if _sha256(raw_path) != row.get("raw_sha256"):
            errors.append(f"{row['raw_path']}: retained raw hash changed")
    return errors
