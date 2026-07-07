"""Body segmenter: heading/paragraph-aware chunks for the embedding index.

Pure function of the body text, so the same body always yields the same
chunks — a requirement for content-hash-based embed idempotency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 1200

_SECTION_HEADING_RE = re.compile(r"^#{2,3}\s")
_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str


@dataclass(frozen=True)
class _Section:
    heading: str | None
    paragraphs: tuple[str, ...]


def _parse_sections(body: str) -> list[_Section]:
    """Split the body on H2/H3 headings; split each section into paragraphs.

    Blank lines are paragraph boundaries except inside code fences, and a
    heading-looking line inside a fence is just code. Paragraph text keeps
    its internal newlines (multi-line list blocks and fenced code stay whole).
    """
    sections: list[_Section] = []
    heading: str | None = None
    paragraphs: list[str] = []
    para_lines: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        nonlocal para_lines
        text = "\n".join(para_lines).strip()
        if text:
            paragraphs.append(text)
        para_lines = []

    def flush_section() -> None:
        nonlocal heading, paragraphs
        flush_paragraph()
        if heading is not None or paragraphs:
            sections.append(_Section(heading=heading, paragraphs=tuple(paragraphs)))
        heading = None
        paragraphs = []

    for line in body.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            para_lines.append(line)
            continue
        if in_fence:
            para_lines.append(line)
            continue
        if _SECTION_HEADING_RE.match(line):
            flush_section()
            heading = line.rstrip()
            continue
        if line.strip() == "":
            flush_paragraph()
            continue
        para_lines.append(line)
    flush_section()
    return sections


def _section_units(section: _Section) -> list[str]:
    """Mergeable units for one section; the heading rides with its first paragraph."""
    if not section.paragraphs:
        return [section.heading] if section.heading is not None else []
    units = list(section.paragraphs)
    if section.heading is not None:
        units[0] = f"{section.heading}\n\n{units[0]}"
    return units


def segment(body: str, target_chars: int = TARGET_CHARS) -> list[Chunk]:
    """Segment a note body into ordered chunks near `target_chars` long.

    Split points are H2/H3 headings and blank-line paragraph boundaries;
    adjacent paragraphs within a section are then merged greedily until
    adding the next one would push past the target. A paragraph is never
    split, so a single long paragraph becomes one oversized chunk, and
    merging never crosses a heading — each heading stays glued to its own
    content.
    """
    chunks: list[str] = []
    for section in _parse_sections(body):
        buffer = ""
        for unit in _section_units(section):
            if not buffer:
                buffer = unit
            elif len(buffer) + 2 + len(unit) <= target_chars:
                buffer = f"{buffer}\n\n{unit}"
            else:
                chunks.append(buffer)
                buffer = unit
        if buffer:
            chunks.append(buffer)
    return [Chunk(ordinal=i, text=text) for i, text in enumerate(chunks)]
