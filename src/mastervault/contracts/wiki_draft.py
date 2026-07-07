"""Wiki draft — writes either a short body addition or a full new entry.

One contract, two modes, selected via `ctx["mode"]` (also a rendered prompt
variable so the model sees which shape is wanted):

- "extend": a single paragraph appended to an existing wiki entry's body
  (ingest routes this to a tier-2 edit-wiki-body review item).
- "new": a full 5-section entry body — Definition (with an
  `**Operating:**` line), Main Insights, Why It Compounds, Cross-Refs, and
  optionally Open Contradictions — for a brand-new concept (ingest routes
  this to a tier-3 new-wiki-page review item).

Section-shape validity is mechanical (headings present, Operating line
present) and belongs here; the ingest pipeline decides the mode.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mastervault.contracts.base import Contract
from mastervault.providers.llm import Tier

EXTEND_MAX_WORDS = 150
REQUIRED_NEW_SECTIONS: tuple[str, ...] = (
    "## Definition",
    "## Main Insights",
    "## Why It Compounds",
    "## Cross-Refs",
)


class WikiDraftOut(BaseModel):
    body_markdown: str = Field(
        description=(
            "For mode=extend: one paragraph to append to the existing entry's body. "
            "For mode=new: the full entry body starting at '## Definition'."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="mode=new only: alternate surface forms readers might search for.",
    )


class WikiDraftContract(Contract[WikiDraftOut]):
    contract_id: ClassVar[str] = "wiki_draft"
    tier: ClassVar[Tier] = "medium"

    def autofix(self, parsed: WikiDraftOut) -> tuple[WikiDraftOut, list[str]]:
        text = parsed.body_markdown.strip()
        if text == parsed.body_markdown:
            return parsed, []
        return (
            WikiDraftOut(body_markdown=text, aliases=list(parsed.aliases)),
            ["stripped whitespace from body_markdown"],
        )

    def hard_fail_checks(self, parsed: WikiDraftOut, ctx: dict[str, Any]) -> list[str]:
        mode = ctx.get("mode", "extend")
        errors: list[str] = []
        if not parsed.body_markdown.strip():
            return ["empty body_markdown"]

        if mode == "extend":
            n_words = len(parsed.body_markdown.split())
            if n_words > EXTEND_MAX_WORDS:
                errors.append(
                    f"extend body has {n_words} words > {EXTEND_MAX_WORDS} "
                    "(should be about one paragraph)"
                )
        elif mode == "new":
            for section in REQUIRED_NEW_SECTIONS:
                if section not in parsed.body_markdown:
                    errors.append(f"missing required section: {section!r}")
            if "**Operating:**" not in parsed.body_markdown:
                errors.append("Definition section is missing a '**Operating:**' line")
        else:
            errors.append(f"unknown mode {mode!r} in ctx (expected 'extend' or 'new')")
        return errors
