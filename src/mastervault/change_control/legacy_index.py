"""Read-only attestation for the exact legacy workspace SQLite index.

The legacy index is derived state, but generation-zero bootstrap still needs
positive evidence that it represents the complete workspace inventory.  This
module opens an existing index without migrating, checkpointing, or otherwise
mutating it; binds SQLite to one pinned inode; and compares every logical row
with the projection produced by the normal synchroniser.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import numpy as np

from mastervault.change_control.managed_generation import (
    INDEX_COUNT_KEYS_V1,
    MAX_INDEX_COUNTS_V1,
    MAX_INDEX_FILE_BYTES_V1,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.storage.base import SCHEMA_VERSION, StorageError
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync import ExactVaultNoteInput, PreparedIndexDocument, prepare_exact_vault_notes


class LegacyIndexIntegrityError(RuntimeError):
    """The legacy index cannot be proven to match its exact workspace projection."""


class LegacyIndexPlatformUnsupportedError(LegacyIndexIntegrityError):
    """The host cannot provide the descriptor-safe SQLite inspection contract."""


@dataclass(frozen=True)
class LegacyIndexAttestation:
    """Pure evidence returned after one complete, read-only index verification."""

    index_file_sha256: str
    index_file_byte_count: int
    projection_fingerprint: str
    logical_index_fingerprint: str
    storage_schema_version: int
    embedding_model_version: str
    embedding_dimensions: int
    counts: tuple[tuple[str, int], ...]


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _stable(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _require_posix_descriptor_contract() -> None:
    required = (
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
        "pread",
        "getuid",
    )
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise LegacyIndexPlatformUnsupportedError(
            "platform cannot pin the legacy SQLite index without following links"
        )
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise LegacyIndexPlatformUnsupportedError(
            "platform lacks descriptor-relative legacy index inspection"
        )
    if os.stat not in os.supports_follow_symlinks:
        raise LegacyIndexPlatformUnsupportedError(
            "platform cannot inspect the legacy index without following links"
        )


def _exact_name(parent_fd: int, name: str, *, label: str) -> None:
    try:
        matches = [entry for entry in os.listdir(parent_fd) if entry.casefold() == name.casefold()]
    except OSError as exc:
        raise LegacyIndexIntegrityError(f"cannot inspect {label} directory") from exc
    if len(matches) != 1 or matches[0] != name:
        raise LegacyIndexIntegrityError(f"{label} path is missing or case-ambiguous")


@dataclass
class _PinnedLegacyIndex:
    path: Path
    parent_fd: int
    file_fd: int
    name: str

    def close(self) -> None:
        file_fd, parent_fd = self.file_fd, self.parent_fd
        self.file_fd = -1
        self.parent_fd = -1
        if file_fd >= 0:
            os.close(file_fd)
        if parent_fd >= 0:
            os.close(parent_fd)

    def verify(self) -> os.stat_result:
        if self.file_fd < 0 or self.parent_fd < 0:
            raise LegacyIndexIntegrityError("legacy index descriptor guard is closed")
        try:
            opened = os.fstat(self.file_fd)
            current = os.stat(
                self.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise LegacyIndexIntegrityError("legacy index path changed while pinned") from exc
        current_parent_fd = -1
        try:
            current_parent_fd = _open_index_parent(self.path)
            current_parent = os.fstat(current_parent_fd)
            pinned_parent = os.fstat(self.parent_fd)
        finally:
            if current_parent_fd >= 0:
                os.close(current_parent_fd)
        if _inode(current_parent) != _inode(pinned_parent):
            raise LegacyIndexIntegrityError(
                "legacy index parent path was substituted while pinned"
            )
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_uid != os.getuid()
            or current.st_uid != os.getuid()
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_mode & 0o022
            or current.st_mode & 0o022
            or opened.st_size <= 0
            or current.st_size <= 0
            or opened.st_size > MAX_INDEX_FILE_BYTES_V1
            or current.st_size > MAX_INDEX_FILE_BYTES_V1
            or _inode(opened) != _inode(current)
        ):
            raise LegacyIndexIntegrityError(
                "legacy SQLite index is not one private regular pinned inode"
            )
        return opened

    def reject_sidecars(self) -> None:
        try:
            names = os.listdir(self.parent_fd)
        except OSError as exc:
            raise LegacyIndexIntegrityError("cannot inspect legacy index sidecars") from exc
        folded = {name.casefold() for name in names}
        for suffix in ("-wal", "-shm", "-journal"):
            if f"{self.name}{suffix}".casefold() in folded:
                raise LegacyIndexIntegrityError(
                    "legacy SQLite index has live or ambiguous transaction sidecars"
                )

    def sha256(self) -> tuple[str, int]:
        before = self.verify()
        digest = hashlib.sha256()
        offset = 0
        try:
            while True:
                block = os.pread(self.file_fd, 1024 * 1024, offset)
                if not block:
                    break
                digest.update(block)
                offset += len(block)
                if offset > MAX_INDEX_FILE_BYTES_V1:
                    raise LegacyIndexIntegrityError(
                        "legacy SQLite index exceeds its fixed size limit"
                    )
        except OSError as exc:
            raise LegacyIndexIntegrityError(
                "legacy SQLite index cannot be hashed through its pinned descriptor"
            ) from exc
        after = self.verify()
        if _stable(before) != _stable(after) or after.st_size != offset:
            raise LegacyIndexIntegrityError("legacy SQLite index changed during hashing")
        return digest.hexdigest(), offset

    def header(self) -> bytes:
        before = self.verify()
        try:
            header = os.pread(self.file_fd, 100, 0)
        except OSError as exc:
            raise LegacyIndexIntegrityError("legacy SQLite header cannot be read") from exc
        after = self.verify()
        if _stable(before) != _stable(after):
            raise LegacyIndexIntegrityError("legacy SQLite index changed during header reading")
        return header


def _open_index_parent(path: Path) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current_fd = -1
    try:
        current_fd = os.open("/", directory_flags)
        for component in path.parts[1:-1]:
            _exact_name(current_fd, component, label="legacy index parent")
            inspected = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(inspected.st_mode):
                raise LegacyIndexIntegrityError("legacy index parent is not a directory")
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            opened = os.fstat(next_fd)
            if not stat.S_ISDIR(opened.st_mode) or _inode(opened) != _inode(inspected):
                os.close(next_fd)
                raise LegacyIndexIntegrityError("legacy index parent changed while traversing")
            os.close(current_fd)
            current_fd = next_fd
        parent_fd = current_fd
        current_fd = -1
        return parent_fd
    except LegacyIndexIntegrityError:
        raise
    except OSError as exc:
        raise LegacyIndexIntegrityError(
            "legacy SQLite index parent cannot be pinned exactly"
        ) from exc
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _pin_index(path: Path) -> _PinnedLegacyIndex:
    _require_posix_descriptor_contract()
    if not path.is_absolute() or "." in path.parts or ".." in path.parts or path.name == "":
        raise LegacyIndexIntegrityError("legacy index path must be absolute and canonical")
    parent_fd = -1
    file_fd = -1
    try:
        parent_fd = _open_index_parent(path)
        _exact_name(parent_fd, path.name, label="legacy index")
        inspected = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        if _inode(opened) != _inode(inspected):
            raise LegacyIndexIntegrityError("legacy index inode changed while opening")
        pinned = _PinnedLegacyIndex(
            path=path,
            parent_fd=parent_fd,
            file_fd=file_fd,
            name=path.name,
        )
        parent_fd = -1
        file_fd = -1
        pinned.verify()
        pinned.reject_sidecars()
        return pinned
    except LegacyIndexIntegrityError:
        raise
    except OSError as exc:
        raise LegacyIndexIntegrityError("legacy SQLite index cannot be pinned exactly") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _descriptor_alias(pinned: _PinnedLegacyIndex) -> Path:
    expected = _inode(pinned.verify())
    for candidate in (
        Path(f"/proc/self/fd/{pinned.file_fd}"),
        Path(f"/dev/fd/{pinned.file_fd}"),
    ):
        probe = -1
        try:
            probe = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK)
            if _inode(os.fstat(probe)) == expected:
                return candidate
        except OSError:
            continue
        finally:
            if probe >= 0:
                os.close(probe)
    raise LegacyIndexPlatformUnsupportedError(
        "platform cannot bind SQLite to the pinned legacy index descriptor"
    )


def _verify_reported_locator(reported: Path, pinned: _PinnedLegacyIndex) -> None:
    if not reported.is_absolute():
        raise LegacyIndexIntegrityError("SQLite reported a non-absolute legacy index locator")
    before = pinned.verify()
    reported_fd = -1
    try:
        reported_fd = os.open(reported, os.O_RDONLY | os.O_NONBLOCK)
        reported_info = os.fstat(reported_fd)
    except OSError as exc:
        raise LegacyIndexIntegrityError(
            "SQLite-reported legacy index locator cannot be inspected"
        ) from exc
    finally:
        if reported_fd >= 0:
            os.close(reported_fd)
    after = pinned.verify()
    if _inode(reported_info) != _inode(before) or _stable(before) != _stable(after):
        raise LegacyIndexIntegrityError(
            "SQLite-reported legacy index locator is not the pinned inode"
        )


def _schema_rows(conn: sqlite3.Connection) -> list[list[Any]]:
    return [
        [str(row[0]), str(row[1]), str(row[2]), None if row[3] is None else str(row[3])]
        for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
        )
    ]


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        raise LegacyIndexIntegrityError("legacy SQLite JSON column is not text")
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise LegacyIndexIntegrityError("legacy SQLite JSON column is invalid") from exc


def _expected_rows(
    prepared: list[PreparedIndexDocument],
    *,
    embedding_model_version: str,
) -> dict[str, list[list[Any]]]:
    documents = sorted((item.doc for item in prepared), key=lambda row: row.doc_id)
    claims = sorted(
        (row for item in prepared for row in item.claims),
        key=lambda row: row.claim_id,
    )
    chunks = sorted(
        (row for item in prepared for row in item.chunks),
        key=lambda row: row.chunk_id,
    )
    aliases = sorted(
        (
            (row.alias, row.wiki_slug, row.domain, item.doc.doc_id)
            for item in prepared
            for row in item.aliases
        ),
        key=lambda row: (row[0], row[1]),
    )
    structural = sorted(
        (row for item in prepared for row in item.structural),
        key=lambda row: row.record_id,
    )
    units = sorted(
        (unit for item in prepared for unit in item.units),
        key=lambda unit: unit.record_id,
    )
    return {
        "documents": [
            [
                row.doc_id,
                row.doc_type,
                row.domain,
                row.rel_path,
                row.title,
                row.frontmatter,
                row.body,
                row.content_hash,
            ]
            for row in documents
        ],
        "claims": [
            [
                row.claim_id,
                row.doc_id,
                row.ordinal,
                row.statement,
                row.confidence,
                row.content_hash,
            ]
            for row in claims
        ],
        "claim_affects": [
            [claim.claim_id, slug]
            for claim, slug in sorted(
                ((claim, slug) for claim in claims for slug in dict.fromkeys(claim.affects)),
                key=lambda item: (item[0].claim_id, item[1]),
            )
        ],
        "wiki_aliases": [list(row) for row in aliases],
        "chunks": [
            [row.chunk_id, row.doc_id, row.ordinal, row.text, row.content_hash]
            for row in chunks
        ],
        "embeddings": [
            [
                unit.record_id,
                unit.record_type,
                unit.doc_id,
                unit.domain,
                unit.content_hash,
                embedding_model_version,
            ]
            for unit in units
        ],
        "structural_records": [
            [
                row.record_id,
                row.doc_id,
                row.ordinal,
                row.record_kind,
                row.text,
                row.asset_sha256,
                row.parsed_artifact_sha256,
                row.parser,
                row.parser_version,
                row.parser_core_version,
                row.parser_profile,
                row.normalization_profile,
                row.model_identity,
                row.resource_limits,
                row.page_number,
                row.block_id,
                row.section_id,
                row.table_id,
                row.row_id,
                row.cell_ids,
                [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in row.evidence
                ],
            ]
            for row in structural
        ],
    }


_ROW_SQL = {
    "documents": (
        "SELECT doc_id,doc_type,domain,rel_path,title,frontmatter,body,content_hash "
        "FROM documents ORDER BY doc_id"
    ),
    "documents_fts": "SELECT doc_id,title,body FROM documents_fts ORDER BY doc_id,title,body",
    "claims": (
        "SELECT claim_id,doc_id,ordinal,statement,confidence,content_hash "
        "FROM claims ORDER BY claim_id"
    ),
    "claims_fts": "SELECT claim_id,statement FROM claims_fts ORDER BY claim_id,statement",
    "claim_affects": "SELECT claim_id,wiki_slug FROM claim_affects ORDER BY claim_id,wiki_slug",
    "wiki_aliases": (
        "SELECT alias,wiki_slug,domain,doc_id FROM wiki_aliases ORDER BY alias,wiki_slug"
    ),
    "chunks": "SELECT chunk_id,doc_id,ordinal,text,content_hash FROM chunks ORDER BY chunk_id",
    "embeddings": (
        "SELECT record_id,record_type,doc_id,domain,content_hash,model_version "
        "FROM embeddings ORDER BY record_id"
    ),
    "structural_records": (
        "SELECT record_id,doc_id,ordinal,record_kind,text,asset_sha256,"
        "parsed_artifact_sha256,parser,parser_version,parser_core_version,"
        "parser_profile,normalization_profile,model_identity,resource_limits,"
        "page_number,block_id,section_id,table_id,row_id,cell_ids,evidence "
        "FROM structural_records ORDER BY record_id"
    ),
    "structural_records_fts": (
        "SELECT record_id,text FROM structural_records_fts ORDER BY record_id,text"
    ),
    "schema_migrations": (
        "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
    ),
}


def _normal_row(table: str, row: tuple[Any, ...]) -> list[Any]:
    values = list(row)
    if table == "documents":
        values[5] = _decode_json(values[5])
    elif table == "structural_records":
        for index in (13, 19, 20):
            values[index] = _decode_json(values[index])
    return values


def _vector_rows(
    conn: sqlite3.Connection,
    *,
    embedding_dimensions: int,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for record_id, raw in conn.execute(
        "SELECT record_id,embedding FROM vec_records ORDER BY record_id"
    ).fetchall():
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise LegacyIndexIntegrityError("legacy vector payload is not a BLOB")
        payload = bytes(raw)
        if len(payload) != embedding_dimensions * 4:
            raise LegacyIndexIntegrityError("legacy vector payload has the wrong dimensions")
        vector = np.frombuffer(payload, dtype="<f4")
        norm = float(np.linalg.norm(vector))
        if not bool(np.all(np.isfinite(vector))) or not np.isclose(
            norm,
            1.0,
            rtol=1e-5,
            atol=1e-6,
        ):
            raise LegacyIndexIntegrityError("legacy vector payload is not finite unit length")
        rows.append(
            [
                str(record_id),
                {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_count": len(payload),
                },
            ]
        )
    return rows


def _reference_schema(
    *,
    embedding_model_version: str,
    embedding_dimensions: int,
) -> tuple[list[list[Any]], list[list[Any]]]:
    backend = SqliteBackend(":memory:")
    try:
        backend.init_schema(embedding_dimensions, embedding_model_version)
        return (
            _schema_rows(backend.conn),
            [
                [int(row[0]), str(row[1]), str(row[2])]
                for row in backend.conn.execute(
                    "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
                ).fetchall()
            ],
        )
    finally:
        backend.close()


def _verify_semantics(
    conn: sqlite3.Connection,
    *,
    prepared: list[PreparedIndexDocument],
    embedding_model_version: str,
    embedding_dimensions: int,
) -> tuple[str, str, tuple[tuple[str, int], ...]]:
    if [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()] != ["ok"]:
        raise LegacyIndexIntegrityError("legacy SQLite index failed integrity_check")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise LegacyIndexIntegrityError("legacy SQLite index failed foreign_key_check")
    meta = {
        str(row[0]): _decode_json(row[1])
        for row in conn.execute("SELECT key,value FROM meta ORDER BY key").fetchall()
    }
    expected_meta = {
        "dimensions": embedding_dimensions,
        "embedding_model": embedding_model_version,
        "schema_version": SCHEMA_VERSION,
    }
    if meta != expected_meta:
        raise LegacyIndexIntegrityError("legacy SQLite index metadata is not exact")
    expected_schema, expected_migrations = _reference_schema(
        embedding_model_version=embedding_model_version,
        embedding_dimensions=embedding_dimensions,
    )
    schema = _schema_rows(conn)
    if schema != expected_schema:
        raise LegacyIndexIntegrityError("legacy SQLite schema differs from packaged migrations")
    rows: dict[str, list[list[Any]]] = {}
    for table, sql in _ROW_SQL.items():
        values = [_normal_row(table, tuple(row)) for row in conn.execute(sql).fetchall()]
        if len(values) > MAX_INDEX_COUNTS_V1:
            raise LegacyIndexIntegrityError(f"legacy index {table} count exceeds its limit")
        rows[table] = values
    if rows["schema_migrations"] != expected_migrations:
        raise LegacyIndexIntegrityError(
            "legacy SQLite migration ledger differs from packaged migrations"
        )
    rows["vec_records"] = _vector_rows(
        conn,
        embedding_dimensions=embedding_dimensions,
    )
    expected = _expected_rows(
        prepared,
        embedding_model_version=embedding_model_version,
    )
    for table, expected_values in expected.items():
        if rows[table] != expected_values:
            raise LegacyIndexIntegrityError(
                f"legacy index {table} rows differ from exact workspace projection"
            )
    expected_claims_fts = sorted([[row[0], row[3]] for row in expected["claims"]])
    expected_documents_fts = sorted(
        [[row[0], row[4], row[6]] for row in expected["documents"]]
    )
    expected_structural_fts = sorted(
        [[row[0], row[4]] for row in expected["structural_records"]]
    )
    for table, expected_values in (
        ("claims_fts", expected_claims_fts),
        ("documents_fts", expected_documents_fts),
        ("structural_records_fts", expected_structural_fts),
    ):
        if rows[table] != expected_values:
            raise LegacyIndexIntegrityError(f"legacy index {table} payloads are not exact")
    expected_record_ids = tuple(
        sorted(unit.record_id for item in prepared for unit in item.units)
    )
    embedding_ids = tuple(str(row[0]) for row in rows["embeddings"])
    vector_ids = tuple(str(row[0]) for row in rows["vec_records"])
    if embedding_ids != expected_record_ids or vector_ids != expected_record_ids:
        raise LegacyIndexIntegrityError(
            "legacy index embedding/vector coverage is incomplete or surplus"
        )
    counts_map = {name: len(rows[name]) for name in INDEX_COUNT_KEYS_V1}
    if any(value > MAX_INDEX_COUNTS_V1 for value in counts_map.values()):
        raise LegacyIndexIntegrityError("legacy index row count exceeds its fixed limit")
    projection_fingerprint = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.legacy-index-projection.v1",
                "embedding_model_version": embedding_model_version,
                "embedding_dimensions": embedding_dimensions,
                "rows": expected,
            }
        )
    ).hexdigest()
    logical_index_fingerprint = hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.legacy-sqlite-logical-index.v1",
                "meta": meta,
                "schema": schema,
                "rows": rows,
            }
        )
    ).hexdigest()
    counts = tuple((name, counts_map[name]) for name in INDEX_COUNT_KEYS_V1)
    return projection_fingerprint, logical_index_fingerprint, counts


def expected_legacy_index_projection_fingerprint(
    *,
    notes: tuple[ExactVaultNoteInput, ...],
    embedding_model_version: str,
    embedding_dimensions: int,
) -> str:
    """Derive the exact logical projection binding without opening SQLite."""

    if not embedding_model_version or not 1 <= embedding_dimensions <= 65_536:
        raise LegacyIndexIntegrityError("legacy index embedding identity is invalid")
    prepared = prepare_exact_vault_notes(notes)
    expected = _expected_rows(
        prepared,
        embedding_model_version=embedding_model_version,
    )
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "namespace": "mastervault.legacy-index-projection.v1",
                "embedding_model_version": embedding_model_version,
                "embedding_dimensions": embedding_dimensions,
                "rows": expected,
            }
        )
    ).hexdigest()


def _attest_pinned(
    *,
    pinned: _PinnedLegacyIndex,
    notes: tuple[ExactVaultNoteInput, ...],
    embedding_model_version: str,
    embedding_dimensions: int,
    expected_index_file_sha256: str,
    expected_index_file_byte_count: int,
) -> LegacyIndexAttestation:
    if not embedding_model_version or not 1 <= embedding_dimensions <= 65_536:
        raise LegacyIndexIntegrityError("legacy index embedding identity is invalid")
    if (
        len(expected_index_file_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_index_file_sha256)
        or not 1 <= expected_index_file_byte_count <= MAX_INDEX_FILE_BYTES_V1
    ):
        raise LegacyIndexIntegrityError("declared legacy index file identity is invalid")
    prepared = prepare_exact_vault_notes(notes)
    backend: SqliteBackend | None = None
    try:
        header = pinned.header()
        if (
            len(header) != 100
            or header[:16] != b"SQLite format 3\x00"
            or header[18] != 1
            or header[19] != 1
        ):
            raise LegacyIndexIntegrityError(
                "legacy SQLite index is not a complete rollback-journal database"
            )
        pinned.reject_sidecars()
        initial_sha, initial_size = pinned.sha256()
        if (
            initial_sha != expected_index_file_sha256
            or initial_size != expected_index_file_byte_count
        ):
            raise LegacyIndexIntegrityError(
                "legacy SQLite index differs from its declared exact file identity"
            )
        alias = _descriptor_alias(pinned)
        backend = SqliteBackend(
            alias,
            read_only=True,
            _read_only_uri=alias.as_uri(),
        )
        databases = backend.conn.execute("PRAGMA database_list").fetchall()
        main = [row for row in databases if str(row[1]) == "main"]
        if len(main) != 1:
            raise LegacyIndexIntegrityError("SQLite did not open exactly one main database")
        _verify_reported_locator(Path(str(main[0][2])), pinned)
        if int(backend.conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise LegacyIndexIntegrityError("legacy SQLite connection is not query-only")
        if str(backend.conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            raise LegacyIndexIntegrityError("legacy SQLite index retains WAL-mode ambiguity")
        serialized = backend.conn.serialize(name="main")
        if (
            len(serialized) != initial_size
            or hashlib.sha256(serialized).hexdigest() != initial_sha
        ):
            raise LegacyIndexIntegrityError(
                "SQLite connection content differs from the pinned legacy index"
            )
        projection, logical, counts = _verify_semantics(
            backend.conn,
            prepared=prepared,
            embedding_model_version=embedding_model_version,
            embedding_dimensions=embedding_dimensions,
        )
        pinned.reject_sidecars()
        final_sha, final_size = pinned.sha256()
        if (final_sha, final_size) != (initial_sha, initial_size):
            raise LegacyIndexIntegrityError("legacy SQLite index changed during attestation")
        return LegacyIndexAttestation(
            index_file_sha256=final_sha,
            index_file_byte_count=final_size,
            projection_fingerprint=projection,
            logical_index_fingerprint=logical,
            storage_schema_version=SCHEMA_VERSION,
            embedding_model_version=embedding_model_version,
            embedding_dimensions=embedding_dimensions,
            counts=counts,
        )
    finally:
        if backend is not None:
            backend.close()


@dataclass
class LegacyIndexAttestationGuard:
    """Live descriptor guard for one already-attested legacy index.

    The guard keeps the exact inode and its parent directory pinned across the
    application-to-authority handoff. ``verify`` is intentionally cheap: the
    complete logical attestation has already run, while each handoff check
    repeats the exact path/inode, sidecar, byte-count, and SHA-256 proof.
    """

    attestation: LegacyIndexAttestation
    _pinned: _PinnedLegacyIndex

    @property
    def index_path(self) -> Path:
        """Exact lexical index path retained by this live guard."""

        return self._pinned.path

    def __enter__(self) -> Self:
        self.verify()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    def verify(self) -> None:
        self._pinned.reject_sidecars()
        identity = self._pinned.sha256()
        if identity != (
            self.attestation.index_file_sha256,
            self.attestation.index_file_byte_count,
        ):
            raise LegacyIndexIntegrityError(
                "legacy SQLite index changed after its complete attestation"
            )

    def close(self) -> None:
        self._pinned.close()


def open_legacy_sqlite_index_attestation_guard(
    *,
    index_path: Path,
    notes: tuple[ExactVaultNoteInput, ...],
    embedding_model_version: str,
    embedding_dimensions: int,
    expected_index_file_sha256: str,
    expected_index_file_byte_count: int,
) -> LegacyIndexAttestationGuard:
    """Fully attest and retain a live no-follow guard for the exact index."""

    pinned: _PinnedLegacyIndex | None = None
    try:
        pinned = _pin_index(index_path)
        attestation = _attest_pinned(
            pinned=pinned,
            notes=notes,
            embedding_model_version=embedding_model_version,
            embedding_dimensions=embedding_dimensions,
            expected_index_file_sha256=expected_index_file_sha256,
            expected_index_file_byte_count=expected_index_file_byte_count,
        )
        guard = LegacyIndexAttestationGuard(attestation=attestation, _pinned=pinned)
        pinned = None
        return guard
    except LegacyIndexIntegrityError:
        raise
    except (OSError, sqlite3.Error, StorageError, TypeError, ValueError) as exc:
        raise LegacyIndexIntegrityError("legacy SQLite index attestation failed") from exc
    finally:
        if pinned is not None:
            pinned.close()


def attest_legacy_sqlite_index(
    *,
    index_path: Path,
    notes: tuple[ExactVaultNoteInput, ...],
    embedding_model_version: str,
    embedding_dimensions: int,
    expected_index_file_sha256: str,
    expected_index_file_byte_count: int,
) -> LegacyIndexAttestation:
    """Prove that one existing SQLite index exactly represents ``notes``.

    The returned value contains no path or timestamp.  A durable application
    receipt can bind it to an operator-approved workspace locator separately.
    """

    try:
        with open_legacy_sqlite_index_attestation_guard(
            index_path=index_path,
            notes=notes,
            embedding_model_version=embedding_model_version,
            embedding_dimensions=embedding_dimensions,
            expected_index_file_sha256=expected_index_file_sha256,
            expected_index_file_byte_count=expected_index_file_byte_count,
        ) as guard:
            return guard.attestation
    except LegacyIndexIntegrityError:
        raise
    except (OSError, sqlite3.Error, StorageError, TypeError, ValueError) as exc:
        raise LegacyIndexIntegrityError("legacy SQLite index attestation failed") from exc


__all__ = [
    "LegacyIndexAttestation",
    "LegacyIndexAttestationGuard",
    "LegacyIndexIntegrityError",
    "LegacyIndexPlatformUnsupportedError",
    "attest_legacy_sqlite_index",
    "expected_legacy_index_projection_fingerprint",
    "open_legacy_sqlite_index_attestation_guard",
]
