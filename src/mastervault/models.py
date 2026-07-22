"""Core data model. Every module codes against these types.

Markdown files with YAML frontmatter are the canonical store; these models are
the typed view of that frontmatter plus the derived index records. Keep this
module dependency-light (pydantic only) — storage, retrieval, and pipelines all
import it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums (closed sets — validators hard-fail outside them)
# ---------------------------------------------------------------------------


class Domain(StrEnum):
    CUSTOMER_SUPPORT = "customer-support"
    SALES_CRM = "sales-crm"
    OPERATIONS = "operations"
    INTERNAL_ADMIN = "internal-admin"


class NoteType(StrEnum):
    SOURCE = "source"
    WIKI = "wiki"
    DECISION = "decision"
    STRATEGY = "strategy"


class SourceType(StrEnum):
    TICKET = "ticket"
    CALL_TRANSCRIPT = "call-transcript"
    EMAIL_THREAD = "email-thread"
    CHAT_LOG = "chat-log"
    FAQ = "faq"
    POLICY = "policy"
    SOP = "sop"
    CHECKLIST = "checklist"
    PROJECT = "project"
    BUG_REPORT = "bug-report"
    INTEGRATION_GUIDE = "integration-guide"
    PROCESS = "process"
    POSTMORTEM = "postmortem"
    LOG = "log"
    CONTRACT = "contract"
    INVOICE = "invoice"
    MEMO = "memo"
    CSAT = "csat"
    CASE_STUDY = "case-study"
    LEAD_NOTE = "lead-note"
    PROPOSAL = "proposal"
    COMPETITOR_NOTE = "competitor-note"
    OTHER = "other"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NoteStatus(StrEnum):
    DRAFT = "draft"
    PROCESSED = "processed"
    ARCHIVED = "archived"


class DecisionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SUPERSEDED = "superseded"


class RecordType(StrEnum):
    """Embeddable / retrievable index units."""

    CLAIM = "claim"
    WIKI = "wiki"
    CHUNK = "chunk"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    CONFLICT = "conflict"


class ChangeType(StrEnum):
    NEW_WIKI_PAGE = "new-wiki-page"
    EDIT_WIKI_BODY = "edit-wiki-body"
    ADD_CROSS_REF = "add-cross-ref"
    ADD_ALIAS = "add-alias"
    ADD_OPEN_CONTRADICTION = "add-open-contradiction"
    DECISION_MEMO = "decision-memo"


# ---------------------------------------------------------------------------
# Claims layer
# ---------------------------------------------------------------------------

CLAIM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-\d{2}$")
WIKI_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Claim(BaseModel):
    """One atomic claim a source asserts. Lives in `key_claims:` frontmatter."""

    id: str = Field(pattern=CLAIM_ID_RE.pattern)
    statement: str = Field(min_length=8)
    confidence: Confidence
    affects: list[str] = Field(default_factory=list)  # wiki slugs (bare basenames)

    @field_validator("affects")
    @classmethod
    def _slugs(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if not WIKI_SLUG_RE.match(s)]
        if bad:
            raise ValueError(f"affects entries must be bare kebab-case slugs, got: {bad}")
        return v


# ---------------------------------------------------------------------------
# Notes (frontmatter views)
# ---------------------------------------------------------------------------


class NoteBase(BaseModel):
    domain: Domain
    type: NoteType
    title: str
    tags: list[str] = Field(default_factory=list)
    status: NoteStatus = NoteStatus.DRAFT
    created: date
    updated: date


class SourceNote(NoteBase):
    type: NoteType = NoteType.SOURCE
    source_type: SourceType
    key_claims: list[Claim] = Field(default_factory=list)
    provenance: str | None = None  # raw-layer path this note was derived from


class WikiEntry(NoteBase):
    type: NoteType = NoteType.WIKI
    aliases: list[str] = Field(default_factory=list)


class DecisionNote(NoteBase):
    type: NoteType = NoteType.DECISION
    decision_status: DecisionStatus = DecisionStatus.OPEN
    review_date: date | None = None
    outcome: str | None = None


class StrategyNote(NoteBase):
    type: NoteType = NoteType.STRATEGY
    quarter: str | None = None  # e.g. "2026-Q2"


# ---------------------------------------------------------------------------
# Index records + retrieval hits
# ---------------------------------------------------------------------------


def content_hash(text: str) -> str:
    """Stable 16-hex-char hash used for change detection and embed idempotency."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Record(BaseModel):
    """One embeddable index unit derived from a note."""

    record_id: str  # "claim:<claim-id>" | "wiki:<domain>:<slug>" | "chunk:<doc-id>#<n>"
    record_type: RecordType
    doc_id: str  # "source:<relpath>" | "wiki:<domain>:<slug>" | "decision:<relpath>"
    domain: Domain
    text: str  # what gets embedded / FTS-indexed
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelRank(BaseModel):
    """Provenance: where a hit ranked in each retrieval channel (1-based; None = absent)."""

    lexical_claims: int | None = None
    lexical_docs: int | None = None
    vector: int | None = None
    graph: int | None = None


class Hit(BaseModel):
    record_id: str
    record_type: RecordType
    doc_id: str
    domain: Domain
    text: str
    rel_path: str | None = None
    confidence: Confidence | None = None  # claims only
    channels: ChannelRank = Field(default_factory=ChannelRank)
    rrf_score: float = 0.0
    rerank_score: float | None = None


# ---------------------------------------------------------------------------
# Review queue (HITL) — file-backed; this model is the frontmatter view
# ---------------------------------------------------------------------------


class ReviewItem(BaseModel):
    id: str
    created: datetime
    producer: str  # "ingest" | "lint" | "deliberate"
    run_id: str
    tier: int = Field(ge=2, le=3)  # tier-1 changes auto-apply and never queue
    target: str  # vault-relative path of the file the change applies to
    change_type: ChangeType
    pattern_key: str = Field(min_length=1)  # batching trust unit; empty is a producer bug
    importance: str = "normal"  # low | normal | high
    rationale: str
    base_hash: str  # content_hash of target at proposal time (drift detection)
    status: ReviewStatus = ReviewStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    resolved: datetime | None = None
    outcome: str | None = None

    @field_validator("id", "target")
    @classmethod
    def _no_path_traversal(cls, value: str) -> str:
        """`id` names a queue file and `target` names a vault file.

        Both are written by LLM-driven producers, so both are untrusted path
        components. The write paths confine them again at the filesystem
        boundary (mastervault.core.paths.resolve_within); refusing them here as
        well means a malformed item cannot even be constructed in-process.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        normalized = stripped.replace("\\", "/")
        if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise ValueError(
                "unsafe path: must be workspace-relative without '..' segments,"
                f" got {value!r}"
            )
        if "\x00" in value:
            raise ValueError("must not contain a NUL byte")
        return value
