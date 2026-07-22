"""Reconcile claim `affects:` slugs against the wiki entries that actually exist.

`affects:` names the wiki concepts a claim bears on, and `mvault lint` treats a
slug with no matching wiki note as a broken reference. The list is populated
from `affects_candidates` — labels the extraction contract proposes off the
document text alone, before anything knows which concepts the vault holds — so
an invented label ("shipping", "warranty", "squeak") lands in the frontmatter
and dangles forever. Nothing downstream ever reconciled it: the route phase
rewrote the note body but wrote its frontmatter back verbatim.

This module closes that gap. Reconciliation only ever *drops*: a slug with a
wiki note is kept byte-for-byte, a slug without one is removed. Nothing is
guessed or remapped — `shipping` is never silently rewritten to `free-shipping`
just because they look alike, because those are different concepts. A claim
whose concept genuinely does not exist yet is already represented elsewhere:
the router tallies it toward a new-concept proposal in the review queue, which
is where a human decides whether the concept should exist at all.

Rewrites go through `surgical_replace_field`, and the replacement block is
rendered with the same serializer that wrote the note, so every claim that
keeps its slugs stays byte-identical and the diff is exactly the dropped lines.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from mastervault.models import NoteType
from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    serialize_frontmatter,
    surgical_replace_field,
)
from mastervault.vaultfs.walker import walk_vault

KEY_CLAIMS_FIELD = "key_claims"


@dataclass(frozen=True)
class DroppedAffect:
    claim_id: str
    slug: str


@dataclass
class AffectsRepair:
    """Result of reconciling one note. `text` is unchanged when nothing dropped."""

    text: str
    dropped: list[DroppedAffect] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.dropped)


def existing_wiki_slugs(vault_dir: Path) -> set[str]:
    """Slugs of the wiki notes on disk, by the same rule the lint check uses.

    Derived from the vault tree rather than the index so reconciliation and
    validation can never disagree about which concepts exist.
    """
    return {
        Path(note.rel_path).stem
        for note in walk_vault(vault_dir).notes
        if note.note_type is NoteType.WIKI
    }


def reconcile_affects(text: str, known_slugs: AbstractSet[str]) -> AffectsRepair:
    """Drop `affects:` entries with no matching wiki note from one note's text.

    Returns the input text untouched when there is nothing to drop, when the
    note has no `key_claims:` block, or when the frontmatter does not parse —
    a malformed note is the frontmatter gate's problem, not this function's.
    """
    try:
        data, _body = parse_frontmatter(text)
    except FrontmatterError:
        return AffectsRepair(text)

    claims = data.get(KEY_CLAIMS_FIELD)
    if not isinstance(claims, list) or not claims:
        return AffectsRepair(text)

    dropped: list[DroppedAffect] = []
    rebuilt: list[object] = []
    for claim in claims:
        if not isinstance(claim, dict):
            rebuilt.append(claim)
            continue
        affects = claim.get("affects")
        if not isinstance(affects, list):
            rebuilt.append(claim)
            continue
        kept = [slug for slug in affects if slug in known_slugs]
        if kept == affects:
            rebuilt.append(claim)
            continue
        claim_id = str(claim.get("id", ""))
        dropped.extend(
            DroppedAffect(claim_id=claim_id, slug=str(slug))
            for slug in affects
            if slug not in known_slugs
        )
        # Re-keying an existing key preserves its position, so only the
        # `affects:` lines move.
        rebuilt.append({**claim, "affects": kept})

    if not dropped:
        return AffectsRepair(text)

    block = serialize_frontmatter({KEY_CLAIMS_FIELD: rebuilt})
    return AffectsRepair(surgical_replace_field(text, KEY_CLAIMS_FIELD, block), dropped)
