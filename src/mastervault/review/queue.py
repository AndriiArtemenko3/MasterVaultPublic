"""File-backed HITL review queue.

One markdown file per queued item: ReviewItem frontmatter + three body
sections — '## Rationale', '## Proposal' (the change payload inside a fenced
code block tagged `replace` or `diff`), and '## Resolution' (empty until the
item is resolved). Proposals may themselves contain triple-backtick fences, so
the writer uses four-backtick fences and the parser accepts any fence of 3+.

Idempotency: sha16(producer|target|change_type|proposal) is the dedupe key;
enqueueing a proposal whose key matches a PENDING item is a no-op (returns
None). Archived items never block a re-enqueue — a rejected proposal may
legitimately come back after the target changed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from mastervault.config import Settings
from mastervault.core.errors import UsageError
from mastervault.core.events import Clock
from mastervault.core.paths import resolve_within
from mastervault.models import ReviewItem, ReviewStatus
from mastervault.vaultfs.frontmatter import (
    join_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)

ProposalKind = Literal["replace", "diff"]

_PROPOSAL_FENCE_RE = re.compile(
    r"(?P<fence>`{3,})(?P<kind>replace|diff)\n(?P<content>.*?)\n(?P=fence)", re.DOTALL
)
_FENCE_LINE_RE = re.compile(r"^(`{3,})")

_RESOLVED_STATUSES = {
    ReviewStatus.APPLIED,
    ReviewStatus.REJECTED,
    ReviewStatus.CONFLICT,
    ReviewStatus.DEFERRED,
}


def dedupe_key(producer: str, target: str, change_type: str, proposal: str) -> str:
    raw = f"{producer}|{target}|{change_type}|{proposal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class LoadedReview:
    path: Path
    item: ReviewItem
    rationale: str
    proposal: str
    kind: ProposalKind
    resolution: str


def _split_sections(body: str) -> dict[str, str]:
    """Map '## Name' -> section text (content between this header and the next).

    Fence-aware: a '## ' line inside a backtick code fence is proposal content,
    not a section boundary. A fence opened with N backticks closes only on a
    bare line of >= N backticks, so 4-backtick proposal fences can safely wrap
    3-backtick blocks.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    open_fence = 0  # backtick count of the open fence; 0 = not inside one

    for line in body.split("\n"):
        m = _FENCE_LINE_RE.match(line.strip())
        if m:
            n = len(m.group(1))
            if open_fence == 0:
                open_fence = n
            elif n >= open_fence and line.strip().strip("`") == "":
                open_fence = 0
        elif open_fence == 0 and line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = line[3:].strip()
            buf = []
            continue
        if current is not None:
            buf.append(line)

    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _render_item(item: ReviewItem, rationale: str, proposal: str, kind: str, resolution: str) -> str:
    fm = serialize_frontmatter(item.model_dump(mode="json", exclude_none=True))
    body = (
        "\n## Rationale\n\n"
        f"{rationale.strip()}\n\n"
        "## Proposal\n\n"
        f"````{kind}\n{proposal}\n````\n\n"
        "## Resolution\n"
        + (f"\n{resolution.strip()}\n" if resolution.strip() else "")
    )
    return join_frontmatter(fm, body)


def _parse_item(path: Path) -> LoadedReview:
    data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    try:
        item = ReviewItem.model_validate(data)
    except ValidationError as exc:
        # A queue file is data on disk: it can be hand-edited or planted, so a
        # rejected field (notably an unsafe `target:`/`id:`) has to surface as a
        # typed, handleable error rather than a pydantic traceback from inside
        # `mvault review apply`.
        raise UsageError(f"{path}: invalid review item: {exc}") from None
    sections = _split_sections(body)
    proposal_section = sections.get("Proposal", "")
    m = _PROPOSAL_FENCE_RE.search(proposal_section)
    if m is None:
        raise UsageError(f"{path}: '## Proposal' has no fenced replace/diff block")
    return LoadedReview(
        path=path,
        item=item,
        rationale=sections.get("Rationale", ""),
        proposal=m.group("content"),
        kind=m.group("kind"),  # type: ignore[arg-type]
        resolution=sections.get("Resolution", ""),
    )


class ReviewQueue:
    def __init__(self, pending_dir: Path, archive_dir: Path, clock: Clock | None = None) -> None:
        self.pending_dir = Path(pending_dir)
        self.archive_dir = Path(archive_dir)
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, settings: Settings) -> ReviewQueue:
        return cls(settings.paths.review_pending, settings.paths.review_archive)

    # -- write side ------------------------------------------------------------

    def enqueue(self, item: ReviewItem, proposal: str, kind: ProposalKind) -> Path | None:
        """Queue one item. Returns its path, or None when deduped against a PENDING twin."""
        if kind not in ("replace", "diff"):
            raise UsageError(f"proposal kind must be 'replace' or 'diff', got {kind!r}")
        if not item.pattern_key or not item.pattern_key.strip():
            raise UsageError(
                f"review item {item.id!r} has an empty pattern_key — producer bug: every "
                "item must carry its batching trust unit"
            )

        key = dedupe_key(item.producer, item.target, str(item.change_type), proposal)
        for _, existing in self.list_items(status=ReviewStatus.PENDING):
            if existing.payload.get("dedupe_key") == key:
                return None

        item = item.model_copy(
            update={
                "status": ReviewStatus.PENDING,
                "payload": {**item.payload, "dedupe_key": key},
            }
        )
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        # `item.id` is producer-derived (it embeds ctx.run_id, which resume
        # reads back out of events.jsonl), so it is untrusted here: joining it
        # raw would let an id of "../../x" write outside the queue entirely.
        path = resolve_within(self.pending_dir, f"{item.id}.md")
        path.write_text(
            _render_item(item, item.rationale, proposal, kind, resolution=""),
            encoding="utf-8",
        )
        return path

    def archive(self, path: Path, outcome: str, note: str = "") -> Path:
        """Resolve an item: stamp resolution frontmatter + text, move to archive/."""
        try:
            status = ReviewStatus(outcome)
        except ValueError:
            raise UsageError(
                f"outcome must be one of {sorted(s.value for s in _RESOLVED_STATUSES)}, "
                f"got {outcome!r}"
            ) from None
        if status not in _RESOLVED_STATUSES:
            raise UsageError(f"outcome {outcome!r} is not a resolved status")

        loaded = _parse_item(Path(path))
        item = loaded.item.model_copy(
            update={"status": status, "resolved": self._clock(), "outcome": outcome}
        )
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        dest = self.archive_dir / Path(path).name
        dest.write_text(
            _render_item(item, loaded.rationale, loaded.proposal, loaded.kind, resolution=note),
            encoding="utf-8",
        )
        Path(path).unlink()
        return dest

    def mark_conflict(self, path: Path, reason: str) -> None:
        """Flip a pending item's status to conflict in place (it stays pending-side)."""
        loaded = _parse_item(Path(path))
        item = loaded.item.model_copy(update={"status": ReviewStatus.CONFLICT})
        Path(path).write_text(
            _render_item(item, loaded.rationale, loaded.proposal, loaded.kind, resolution=reason),
            encoding="utf-8",
        )

    # -- read side ---------------------------------------------------------------

    def list_items(
        self,
        status: ReviewStatus | str | None = None,
        pattern: str | None = None,
    ) -> list[tuple[Path, ReviewItem]]:
        """Pending-side items, oldest first, optionally filtered by status/pattern_key."""
        if status is not None:
            status = ReviewStatus(status)
        out: list[tuple[Path, ReviewItem]] = []
        if not self.pending_dir.is_dir():
            return out
        for path in sorted(self.pending_dir.glob("*.md")):
            data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            item = ReviewItem.model_validate(data)
            if status is not None and item.status != status:
                continue
            if pattern is not None and item.pattern_key != pattern:
                continue
            out.append((path, item))
        out.sort(key=lambda pair: pair[1].created)
        return out

    def load(self, path: Path) -> LoadedReview:
        """Full parse of one item file: frontmatter + all three body sections."""
        return _parse_item(Path(path))
