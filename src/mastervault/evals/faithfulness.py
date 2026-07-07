"""Mechanical citation-validity checker for `ask`-style grounded answers.

`mastervault.pipelines.ask` renders its grounded synthesis with inline
citations shaped `[<record-id>]` (e.g. `[claim:refund-policy-01]`) and gates
them at generation time against the live evidence pool. This module is the
same mechanical check — extract every bracketed token, test it against a
pool of valid ids — as a standalone, reusable, offline auditor: point it at
an already-generated answer plus whatever pool it should have been grounded
in (a stored evidence snapshot, a set of claim ids pulled fresh from the
index, anything) and get back which citations actually resolve.

This does not call an LLM and does not touch storage; both inputs are plain
strings/collections, so it is fully unit-testable on synthetic examples.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field

CITATION_RE = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class CitationReport:
    cited_ids: list[str] = field(default_factory=list)
    """Every `[<id>]` token found, in order of appearance, duplicates included."""
    valid_ids: list[str] = field(default_factory=list)
    """Distinct cited ids that resolve against the pool, first-seen order."""
    invalid_ids: list[str] = field(default_factory=list)
    """Distinct cited ids that do NOT resolve against the pool, first-seen order."""

    @property
    def valid(self) -> bool:
        """True iff every citation resolves. An answer with zero citations is
        vacuously valid — "no citations" is a coverage problem, not a
        faithfulness violation; callers that care about coverage should also
        check `cited_ids`.
        """
        return not self.invalid_ids

    @property
    def precision(self) -> float:
        """Fraction of *citation occurrences* (not distinct ids) that resolve.
        1.0 when there are no citations at all (vacuously faithful).
        """
        if not self.cited_ids:
            return 1.0
        valid_set = set(self.valid_ids)
        resolved = sum(1 for c in self.cited_ids if c in valid_set)
        return resolved / len(self.cited_ids)


def extract_citations(text: str) -> list[str]:
    """Every `[<id>]` bracket token in `text`, in order, duplicates included."""
    return CITATION_RE.findall(text)


def check_citations(answer_markdown: str, valid_pool: Collection[str]) -> CitationReport:
    """Extract citations from `answer_markdown` and classify each against `valid_pool`.

    `valid_pool` is whatever id space the answer's citations are supposed to
    resolve against — typically the record_ids (`claim:<id>`, `chunk:<doc>#n`,
    `wiki:<domain>:<slug>`) of the evidence pool an `ask` run retrieved.
    """
    pool = set(valid_pool)
    cited = extract_citations(answer_markdown)

    valid_ids: list[str] = []
    invalid_ids: list[str] = []
    seen_valid: set[str] = set()
    seen_invalid: set[str] = set()
    for token in cited:
        if token in pool:
            if token not in seen_valid:
                seen_valid.add(token)
                valid_ids.append(token)
        elif token not in seen_invalid:
            seen_invalid.add(token)
            invalid_ids.append(token)

    return CitationReport(cited_ids=cited, valid_ids=valid_ids, invalid_ids=invalid_ids)
