"""Frontmatter primitives: split, parse (with jam-repair), serialize, surgical edit.

The vault is file-canonical, so every write path must be byte-conservative.
`surgical_replace_field` is the only sanctioned way to rewrite one frontmatter
field: it splices new bytes into the exact span of the old field and never
round-trips the rest of the file through a YAML dumper.
"""

from __future__ import annotations

import re

import yaml

FENCE = "---"

# A top-level frontmatter field line: key at column 0, then a colon.
_FIELD_LINE_RE = re.compile(r"^(?P<key>[A-Za-z0-9_-]+):")

# A candidate jam line: `key: value` where value contains a further unquoted
# `: ` (YAML then sees a nested mapping mid-scalar and errors out).
_JAM_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_-]+):[ \t](?P<value>.+)$")


class FrontmatterError(ValueError):
    """Raised when frontmatter is absent or cannot be parsed even after repair."""


def split_frontmatter(text: str) -> tuple[str, str, bool]:
    """Split a note into (yaml_str, body, had_frontmatter).

    Frontmatter is recognised only when the file opens with a `---` fence line
    and a later line is exactly `---` (modulo trailing whitespace). `yaml_str`
    is the raw text between the fences; `body` is everything after the closing
    fence line. Files without frontmatter return ("", text, False).
    """
    span = _frontmatter_span(text)
    if span is None:
        return ("", text, False)
    yaml_start, yaml_end, body_start = span
    return (text[yaml_start:yaml_end], text[body_start:], True)


def _frontmatter_span(text: str) -> tuple[int, int, int] | None:
    """Return (yaml_start, yaml_end, body_start) offsets, or None.

    yaml_end is the offset of the closing fence line's first character;
    body_start is the offset just past the closing fence line (and its
    newline, when present).
    """
    if not text.startswith(FENCE):
        return None
    first_nl = text.find("\n")
    if first_nl < 0 or text[:first_nl].strip() != FENCE:
        return None
    yaml_start = first_nl + 1
    idx = yaml_start
    while idx <= len(text):
        line_end = text.find("\n", idx)
        if line_end < 0:
            line = text[idx:]
            if line.strip() == FENCE:
                return (yaml_start, idx, len(text))
            return None
        if text[idx:line_end].strip() == FENCE:
            return (yaml_start, idx, line_end + 1)
        idx = line_end + 1
    return None


def repair_yaml(yaml_str: str) -> str:
    """Minimal jam-repair for common frontmatter breaks.

    Currently one class: an unquoted colon inside a plain scalar value
    (`title: RAG: the good parts`). The value is re-emitted double-quoted.
    Lines whose value is already quoted, a flow collection, a comment, or a
    block-scalar indicator are left alone. Idempotent by construction: quoted
    values no longer match the jam pattern.
    """
    out_lines: list[str] = []
    for line in yaml_str.split("\n"):
        m = _JAM_LINE_RE.match(line)
        if m is None:
            out_lines.append(line)
            continue
        value = m.group("value").rstrip()
        if (
            ": " not in value
            or value[0] in "\"'[{|>&*"
            or value.startswith("- ")
            or value.startswith("#")
        ):
            out_lines.append(line)
            continue
        quoted = value.replace("\\", "\\\\").replace('"', '\\"')
        out_lines.append(f'{m.group("indent")}{m.group("key")}: "{quoted}"')
    return "\n".join(out_lines)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a note into (frontmatter_dict, body).

    Tries a strict `yaml.safe_load` first; on failure runs the jam-repair pass
    and retries once. Raises FrontmatterError when the note has no frontmatter
    or the YAML still fails after repair.
    """
    yaml_str, body, had = split_frontmatter(text)
    if not had:
        raise FrontmatterError("note has no frontmatter fences")
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        repaired = repair_yaml(yaml_str)
        if repaired == yaml_str:
            raise FrontmatterError(f"unparseable frontmatter: {exc}") from exc
        try:
            data = yaml.safe_load(repaired)
        except yaml.YAMLError as exc2:
            raise FrontmatterError(f"unparseable frontmatter after repair: {exc2}") from exc2
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError(f"frontmatter is {type(data).__name__}, expected mapping")
    return (data, body)


def serialize_frontmatter(data: dict) -> str:
    """Render a frontmatter dict to YAML (insertion order kept, unicode kept)."""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000)


def join_frontmatter(yaml_str: str, body: str) -> str:
    """Assemble full note text from a YAML string and a body."""
    if yaml_str and not yaml_str.endswith("\n"):
        yaml_str += "\n"
    return f"{FENCE}\n{yaml_str}{FENCE}\n{body}"


def _field_span(text: str, field: str) -> tuple[int, int]:
    """Byte span [start, end) of one top-level frontmatter field within `text`.

    The span covers the `field:` line plus every continuation line (blank,
    indented, or column-0 `-` list item), including the trailing newline of
    the last covered line. Raises KeyError when the field is absent and
    FrontmatterError when the note has no frontmatter.
    """
    span = _frontmatter_span(text)
    if span is None:
        raise FrontmatterError("note has no frontmatter fences")
    yaml_start, yaml_end, _ = span

    idx = yaml_start
    field_start = -1
    while idx < yaml_end:
        line_end = text.find("\n", idx, yaml_end)
        if line_end < 0:
            line_end = yaml_end
        line = text[idx:line_end]
        m = _FIELD_LINE_RE.match(line)
        if m and m.group("key") == field:
            field_start = idx
            idx = min(line_end + 1, yaml_end)
            break
        idx = line_end + 1
    if field_start < 0:
        raise KeyError(f"frontmatter field not found: {field!r}")

    # Extend through continuation lines.
    field_end = idx
    while idx < yaml_end:
        line_end = text.find("\n", idx, yaml_end)
        if line_end < 0:
            line_end = yaml_end
        line = text[idx:line_end]
        if line.strip() == "" or line[0] in (" ", "\t", "-"):
            idx = min(line_end + 1, yaml_end)
            field_end = idx
            continue
        break
    return (field_start, field_end)


def extract_field_block(text: str, field: str) -> str:
    """Return the raw bytes of one top-level frontmatter field (incl. newline)."""
    start, end = _field_span(text, field)
    return text[start:end]


def surgical_replace_field(text: str, field: str, new_yaml_block: str) -> str:
    """Replace exactly one top-level frontmatter field, preserving every other byte.

    `new_yaml_block` must be the full rendered field (e.g. "tags: [a, b]\\n" or
    a multi-line `key_claims:` block); a trailing newline is added when missing.
    No YAML round-trip happens outside the replaced span, so replacing a field
    with its current block returns the input byte-for-byte.
    """
    if not new_yaml_block.endswith("\n"):
        new_yaml_block += "\n"
    start, end = _field_span(text, field)
    return text[:start] + new_yaml_block + text[end:]
