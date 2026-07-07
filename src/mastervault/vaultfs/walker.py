"""Vault tree walker: enumerate indexable notes, collect skips with reasons."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from mastervault.models import Domain, NoteType, content_hash
from mastervault.vaultfs.frontmatter import FrontmatterError, parse_frontmatter

REQUIRED_FIELDS = ("domain", "type")


class NoteRef(NamedTuple):
    abs_path: Path
    rel_path: str
    note_type: NoteType
    domain: Domain
    content_hash: str  # hash of the full file text (frontmatter + body)


class SkippedFile(NamedTuple):
    rel_path: str
    reason: str


class WalkResult(NamedTuple):
    notes: list[NoteRef]
    skipped: list[SkippedFile]


def walk_vault(vault_dir: Path | str) -> WalkResult:
    """Enumerate every indexable .md note under vault_dir.

    Non-.md files and anything under a dot-directory (or a dotfile itself)
    are ignored silently — they are not vault content. Markdown files that
    fail the gate (unreadable, unparseable frontmatter, missing or invalid
    `domain:` / `type:`) land in `skipped` with a reason so callers can
    surface them instead of indexing garbage. Output is sorted by rel_path,
    so the walk is deterministic.
    """
    vault_dir = Path(vault_dir)
    notes: list[NoteRef] = []
    skipped: list[SkippedFile] = []

    candidates = sorted(
        (p.relative_to(vault_dir).as_posix(), p) for p in vault_dir.rglob("*.md") if p.is_file()
    )
    for rel_path, path in candidates:
        if any(part.startswith(".") for part in rel_path.split("/")):
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(SkippedFile(rel_path, f"unreadable: {exc}"))
            continue

        try:
            data, _body = parse_frontmatter(text)
        except FrontmatterError as exc:
            skipped.append(SkippedFile(rel_path, str(exc)))
            continue

        missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
        if missing:
            skipped.append(SkippedFile(rel_path, f"missing required fields: {', '.join(missing)}"))
            continue

        try:
            note_type = NoteType(data["type"])
        except ValueError:
            skipped.append(SkippedFile(rel_path, f"invalid type: {data['type']!r}"))
            continue
        try:
            domain = Domain(data["domain"])
        except ValueError:
            skipped.append(SkippedFile(rel_path, f"invalid domain: {data['domain']!r}"))
            continue

        notes.append(
            NoteRef(
                abs_path=path,
                rel_path=rel_path,
                note_type=note_type,
                domain=domain,
                content_hash=content_hash(text),
            )
        )

    return WalkResult(notes=notes, skipped=skipped)
