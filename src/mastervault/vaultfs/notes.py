"""Typed read/write of vault notes plus slug and title helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple

from mastervault.models import (
    DecisionNote,
    NoteBase,
    NoteType,
    SourceNote,
    StrategyNote,
    WikiEntry,
)
from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    join_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)

MODEL_BY_TYPE: dict[NoteType, type[NoteBase]] = {
    NoteType.SOURCE: SourceNote,
    NoteType.WIKI: WikiEntry,
    NoteType.DECISION: DecisionNote,
    NoteType.STRATEGY: StrategyNote,
}

_H1_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

SLUG_HASH_LEN = 8


class LoadedNote(NamedTuple):
    model: NoteBase
    body: str


def slugify(text: str, max_len: int = 60) -> str:
    """Kebab-case slug. Truncation appends a sha1 tail so long inputs never collide.

    Inputs whose slugs fit within max_len are returned as-is; longer ones are
    cut to leave room for `-<8-hex sha1 of the full slug>`, which keeps two
    long titles with a common prefix distinguishable.
    """
    slug = _NON_SLUG_RE.sub("-", text.lower()).strip("-")
    if not slug:
        slug = "untitled"
    if len(slug) <= max_len:
        return slug
    tail = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:SLUG_HASH_LEN]
    head = slug[: max_len - SLUG_HASH_LEN - 1].rstrip("-")
    return f"{head}-{tail}"


def extract_title(body: str, fallback: str) -> str:
    """First H1 heading in the body, else the fallback (usually the filename stem)."""
    m = _H1_RE.search(body)
    if m:
        return m.group("title")
    return fallback


def read_note(path: Path | str) -> LoadedNote:
    """Read a note file into its typed model plus the raw body.

    Dispatches on the frontmatter `type:` field. A missing `title` is filled
    from the body's first H1, else the filename stem. Raises FrontmatterError
    for missing/unparseable frontmatter or an unknown `type:`; pydantic raises
    ValidationError for bad field values.
    """
    path = Path(path)
    data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    raw_type = data.get("type")
    if not isinstance(raw_type, str):
        # A missing or non-string `type:` is the same defect as an unknown one.
        raise FrontmatterError(f"unknown note type: {raw_type!r}")
    try:
        note_type = NoteType(raw_type)
    except ValueError:
        raise FrontmatterError(f"unknown note type: {raw_type!r}") from None
    if not data.get("title"):
        data = {**data, "title": extract_title(body, path.stem)}
    model = MODEL_BY_TYPE[note_type].model_validate(data)
    return LoadedNote(model=model, body=body)


def write_note(path: Path | str, model: NoteBase, body: str) -> None:
    """Write a typed note to disk as frontmatter + body.

    The writer is deterministic (field order = model order, dates ISO, enums
    as values, None fields dropped) but not byte-preserving; use
    `surgical_replace_field` when only one field must change in an existing
    file.
    """
    path = Path(path)
    data = model.model_dump(mode="json", exclude_none=True)
    yaml_str = serialize_frontmatter(data)
    normalized_body = body.strip("\n")
    text = join_frontmatter(yaml_str, f"\n{normalized_body}\n" if normalized_body else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
