"""Parser-independent document models for page-grounded source evidence.

The v1 schema is intentionally modest: the baseline pypdf parser preserves one
text block per page.  Layout regions, tables, and OCR metadata can be added by
future parsers without coupling the canonical vault to a parser-specific type.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DOCUMENT_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCK_ID_RE = re.compile(r"^page-(?P<page>\d{4})-block-(?P<block>\d{4})$")


def _workspace_relative(value: str) -> str:
    """Validate a portable workspace-relative path stored in frontmatter."""
    raw = value.strip()
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if (
        not raw
        or "\x00" in raw
        or candidate.is_absolute()
        or Path(raw).is_absolute()
        or Path(raw).drive
        or ".." in candidate.parts
    ):
        raise ValueError(f"must be a safe workspace-relative path, got {value!r}")
    return candidate.as_posix()


class DocumentBlockType(StrEnum):
    """Closed v1 block vocabulary; pypdf emits only ``page_text``."""

    PAGE_TEXT = "page_text"
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    IMAGE = "image"
    UNKNOWN = "unknown"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceAssetRef(_StrictModel):
    """Reference to an immutable, content-addressed source PDF."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    stored_path: str
    media_type: Literal["application/pdf"] = "application/pdf"
    original_filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)

    @field_validator("stored_path")
    @classmethod
    def _safe_stored_path(cls, value: str) -> str:
        return _workspace_relative(value)

    @field_validator("original_filename")
    @classmethod
    def _plain_filename(cls, value: str) -> str:
        portable = PurePosixPath(value.replace("\\", "/"))
        if "\x00" in value or portable.name != value or value in {".", ".."}:
            raise ValueError("must be a plain filename without path components")
        return value


class ParsedDocumentRef(_StrictModel):
    """Reference to a verified, schema-versioned parsed-document artefact."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_profile: str = Field(min_length=1)
    artifact_path: str
    artifact_sha256: str = Field(pattern=SHA256_RE.pattern)

    @field_validator("artifact_path")
    @classmethod
    def _safe_artifact_path(cls, value: str) -> str:
        return _workspace_relative(value)


class DocumentBlock(_StrictModel):
    """One ordered, addressable unit of text on a page."""

    block_id: str = Field(pattern=BLOCK_ID_RE.pattern)
    block_type: DocumentBlockType
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=1)
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("block text must contain a non-whitespace character")
        return value


class ParsedPage(_StrictModel):
    """One physical PDF page, numbered from one."""

    page_number: int = Field(ge=1)
    blocks: list[DocumentBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def _block_consistency(self) -> ParsedPage:
        expected_order = list(range(1, len(self.blocks) + 1))
        actual_order = [block.reading_order for block in self.blocks]
        if actual_order != expected_order:
            raise ValueError(f"page {self.page_number}: reading_order must be contiguous from one")
        mismatched = [
            block.block_id for block in self.blocks if block.page_number != self.page_number
        ]
        if mismatched:
            raise ValueError(
                f"page {self.page_number}: blocks carry another page number: {mismatched}"
            )
        encoded_elsewhere: list[str] = []
        for block in self.blocks:
            match = BLOCK_ID_RE.fullmatch(block.block_id)
            if match is None:  # field validation already guards this path
                raise ValueError(f"invalid block id: {block.block_id!r}")
            if int(match.group("page")) != self.page_number:
                encoded_elsewhere.append(block.block_id)
        if encoded_elsewhere:
            raise ValueError(
                f"page {self.page_number}: block ids encode another page: {encoded_elsewhere}"
            )
        return self

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


class ParseWarning(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


class ParsedDocument(_StrictModel):
    """Canonical parser-neutral representation persisted as deterministic JSON."""

    schema_version: Literal[1] = 1
    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_profile: str = Field(min_length=1)
    pages: list[ParsedPage] = Field(min_length=1)
    warnings: list[ParseWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _document_consistency(self) -> ParsedDocument:
        expected_pages = list(range(1, len(self.pages) + 1))
        actual_pages = [page.page_number for page in self.pages]
        if actual_pages != expected_pages:
            raise ValueError("page_number must be contiguous from one in physical order")
        block_ids = [block.block_id for page in self.pages for block in page.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id values must be unique within a parsed document")
        return self

    def flattened_text(self) -> str:
        """Compatibility view matching the v0.2 PDF flattening convention."""
        return "\n\n".join(page.text for page in self.pages).strip()

    def block_index(self) -> dict[str, DocumentBlock]:
        return {block.block_id: block for page in self.pages for block in page.blocks}

    def prompt_text(self) -> str:
        """Addressable blocks supplied to the page-grounded extraction prompt."""
        parts: list[str] = []
        for page in self.pages:
            for block in page.blocks:
                parts.append(f"[BLOCK {block.block_id} | PAGE {page.page_number}]\n{block.text}")
        return "\n\n".join(parts)


class EvidenceRef(_StrictModel):
    """A claim's mechanically resolved supporting span in a parsed PDF block."""

    asset_sha256: str = Field(pattern=SHA256_RE.pattern)
    page_number: int = Field(ge=1)
    block_id: str = Field(pattern=BLOCK_ID_RE.pattern)
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @field_validator("quote")
    @classmethod
    def _meaningful_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence quote must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def _ordered_offsets(self) -> EvidenceRef:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self
