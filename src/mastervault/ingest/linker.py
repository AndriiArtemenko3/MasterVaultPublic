"""Idempotent wikilink insertion into a freshly-written source note body.

`alias` and `slug` are never the same string and must never be confused: the
regex search targets `alias` — a wiki concept's spaced natural-language
surface form as stored in the alias index (e.g. "refund window") — and the
replacement text is built from `slug`, the concept's bare kebab identifier
(e.g. "refund-window"). Searching for the slug in prose, or linking with the
alias, both silently fail to match real writing.

Idempotent by construction: before scanning, the whole body is checked for an
existing `[[slug]]` link anywhere; if one is already present the call is a
no-op, so re-running the linker over an already-linked note (or re-ingesting
the same source) never inserts a duplicate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class LinkResult:
    body: str
    applied: bool
    matched_alias: str | None = None
    slug: str | None = None


def _existing_link_re(slug: str) -> re.Pattern[str]:
    return re.compile(rf"\[\[{re.escape(slug)}(\|[^\]]*)?\]\]")


def insert_wikilink(body: str, alias: str, slug: str) -> LinkResult:
    """Insert `[[slug]]` at the first natural-language mention of `alias`.

    Skips fenced code blocks, heading lines, and any text already inside an
    existing `[[...]]` link. Returns the body unchanged (applied=False) when
    no eligible mention is found, or when `slug` is already linked anywhere.
    """
    if not alias.strip() or not slug.strip():
        return LinkResult(body=body, applied=False, matched_alias=alias, slug=slug)
    if _existing_link_re(slug).search(body):
        return LinkResult(body=body, applied=False, matched_alias=alias, slug=slug)

    mention_re = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
    existing_link_re = re.compile(r"\[\[.*?\]\]")

    lines = body.split("\n")
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence or line.lstrip().startswith("#"):
            continue

        occupied = [m.span() for m in existing_link_re.finditer(line)]
        for m in mention_re.finditer(line):
            if any(start <= m.start() < end for start, end in occupied):
                continue
            new_line = f"{line[: m.start()]}[[{slug}]]{line[m.end() :]}"
            lines[i] = new_line
            return LinkResult(
                body="\n".join(lines), applied=True, matched_alias=alias, slug=slug
            )

    return LinkResult(body=body, applied=False, matched_alias=alias, slug=slug)
