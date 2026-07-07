"""vaultfs: the file-canonical layer.

Markdown files with YAML frontmatter are the source of truth; everything else
in the system is a derived index. This package owns reading, writing, walking,
and segmenting those files.
"""

from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    extract_field_block,
    join_frontmatter,
    parse_frontmatter,
    repair_yaml,
    serialize_frontmatter,
    split_frontmatter,
    surgical_replace_field,
)
from mastervault.vaultfs.notes import (
    LoadedNote,
    extract_title,
    read_note,
    slugify,
    write_note,
)
from mastervault.vaultfs.segmenter import Chunk, segment
from mastervault.vaultfs.walker import NoteRef, SkippedFile, WalkResult, walk_vault

__all__ = [
    "Chunk",
    "FrontmatterError",
    "LoadedNote",
    "NoteRef",
    "SkippedFile",
    "WalkResult",
    "extract_field_block",
    "extract_title",
    "join_frontmatter",
    "parse_frontmatter",
    "read_note",
    "repair_yaml",
    "segment",
    "serialize_frontmatter",
    "slugify",
    "split_frontmatter",
    "surgical_replace_field",
    "walk_vault",
    "write_note",
]
