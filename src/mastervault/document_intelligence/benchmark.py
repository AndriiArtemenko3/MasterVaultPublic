"""Runtime-safe contracts for deterministic Larkstead PDF renditions.

These models intentionally contain no evaluator answers.  Parser-hidden
layout truth and change-impact labels live under :mod:`mastervault.evals` and
are loaded only by evaluation/test code.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"
REQUIRED_VARIANTS = {
    "single-column",
    "two-column",
    "table-emphasis",
    "repeated-furniture",
}


def repo_relative_path(value: str) -> str:
    """Return a normalized repository-relative file path without anchored inputs."""
    raw = value.strip()
    candidate = PurePosixPath(raw.replace("\\", "/"))
    windows_candidate = PureWindowsPath(raw)
    if (
        not raw
        or "\x00" in raw
        or not candidate.parts
        or candidate.is_absolute()
        or windows_candidate.is_absolute()
        or bool(windows_candidate.drive)
        or Path(raw).is_absolute()
        or Path(raw).drive
        or ".." in candidate.parts
    ):
        raise ValueError(f"must be a safe repository-relative path, got {value!r}")
    return candidate.as_posix()


class StrictBenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkSplit(StrEnum):
    DEVELOPMENT = "development"
    HELD_OUT = "held-out"


class DocumentRole(StrEnum):
    POLICY = "policy"
    PROCESS = "process"
    PROPOSAL = "proposal"
    FINANCIAL_RECORD = "financial-record"
    POSTMORTEM = "postmortem"
    FAQ = "faq"
    SOP = "sop"
    MEMO = "memo"
    INTEGRATION_GUIDE = "integration-guide"
    RECEIVING_LOG = "receiving-log"


class DocumentAuthority(StrEnum):
    PRIMARY = "primary"
    DELEGATED = "delegated"
    TRANSACTIONAL = "transactional"
    INFORMATIONAL = "informational"


class LayoutProfileSpec(StrictBenchmarkModel):
    variant_id: str = Field(pattern=ID_PATTERN)
    description: str = Field(min_length=1)
    columns: Literal[1, 2]
    landscape: bool
    table_emphasis: bool
    repeated_furniture: bool
    force_page_break: bool
    layout_features: list[str] = Field(min_length=1)

    @field_validator("layout_features")
    @classmethod
    def _unique_features(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("layout_features must be unique")
        return values


class SemanticDocumentSpec(StrictBenchmarkModel):
    document_family_id: str = Field(pattern=ID_PATTERN)
    semantic_document_id: str = Field(pattern=ID_PATTERN)
    version_id: str = Field(pattern=ID_PATTERN)
    effective_from: date
    effective_to: date | None = None
    document_role: DocumentRole
    authority: DocumentAuthority
    supersedes_version_id: str | None = Field(default=None, pattern=ID_PATTERN)
    split: BenchmarkSplit
    storyline: str | None = Field(default=None, pattern=r"^SL[1-5]$")
    source_path: str

    @field_validator("source_path")
    @classmethod
    def _source_path(cls, value: str) -> str:
        return repo_relative_path(value)

    @model_validator(mode="after")
    def _date_range(self) -> SemanticDocumentSpec:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self


class BenchmarkSizeBudget(StrictBenchmarkModel):
    max_total_pdf_bytes: int = Field(ge=1)
    max_single_pdf_bytes: int = Field(ge=1)
    target_semantic_documents: Literal[6] = 6
    variants_per_document: Literal[4] = 4

    @model_validator(mode="after")
    def _coherent_budget(self) -> BenchmarkSizeBudget:
        if self.max_single_pdf_bytes > self.max_total_pdf_bytes:
            raise ValueError("single-PDF budget cannot exceed total PDF budget")
        return self


class PdfBenchmarkSpec(StrictBenchmarkModel):
    schema_version: Literal[1] = 1
    dataset_id: Literal["larkstead-pdf-layout-benchmark"]
    description: str = Field(min_length=1)
    license: Literal["CC-BY-4.0"]
    size_budget: BenchmarkSizeBudget
    layout_profiles: list[LayoutProfileSpec] = Field(min_length=4, max_length=4)
    semantic_documents: list[SemanticDocumentSpec] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _bounded_family_split(self) -> PdfBenchmarkSpec:
        variants = {profile.variant_id for profile in self.layout_profiles}
        if variants != REQUIRED_VARIANTS:
            raise ValueError(
                f"layout profiles must be exactly {sorted(REQUIRED_VARIANTS)}, got {sorted(variants)}"
            )
        family_ids = [document.document_family_id for document in self.semantic_documents]
        semantic_ids = [document.semantic_document_id for document in self.semantic_documents]
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("one semantic document per family is required in this bounded set")
        if len(semantic_ids) != len(set(semantic_ids)):
            raise ValueError("semantic_document_id values must be unique")
        split_counts = {
            split: sum(document.split == split for document in self.semantic_documents)
            for split in BenchmarkSplit
        }
        if split_counts != {BenchmarkSplit.DEVELOPMENT: 3, BenchmarkSplit.HELD_OUT: 3}:
            raise ValueError("benchmark must contain exactly three families in each split")
        return self


class PdfRenditionManifest(StrictBenchmarkModel):
    asset_id: str = Field(pattern=ID_PATTERN)
    variant_id: str = Field(pattern=ID_PATTERN)
    media_type: Literal["application/pdf"] = "application/pdf"
    pdf_path: str
    pdf_sha256: str = Field(pattern=SHA256_PATTERN)
    pdf_bytes: int = Field(ge=1)
    page_count: int = Field(ge=1)
    page_size_points: list[float] = Field(min_length=2, max_length=2)
    native_text: Literal[True] = True
    encrypted: Literal[False] = False
    render_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    layout_features: list[str] = Field(min_length=1)

    @field_validator("pdf_path")
    @classmethod
    def _pdf_path(cls, value: str) -> str:
        return repo_relative_path(value)


class SemanticDocumentManifest(SemanticDocumentSpec):
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_projection_sha256: str = Field(pattern=SHA256_PATTERN)
    source_bytes: int = Field(ge=1)
    renditions: list[PdfRenditionManifest] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _renditions(self) -> SemanticDocumentManifest:
        if {rendition.variant_id for rendition in self.renditions} != REQUIRED_VARIANTS:
            raise ValueError("each semantic document must contain all four layout variants")
        return self


class GeneratorIdentity(StrictBenchmarkModel):
    path: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    reportlab_version: str = Field(min_length=1)
    deterministic_pdf: Literal[True] = True
    cross_platform_byte_identity_promised: Literal[False] = False

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return repo_relative_path(value)


class PdfBenchmarkManifest(StrictBenchmarkModel):
    schema_version: Literal[2] = 2
    dataset_id: Literal["larkstead-pdf-layout-benchmark"]
    description: str = Field(min_length=1)
    license: Literal["CC-BY-4.0"]
    spec_path: str
    spec_sha256: str = Field(pattern=SHA256_PATTERN)
    generator: GeneratorIdentity
    size_budget: BenchmarkSizeBudget
    total_pdf_bytes: int = Field(ge=1)
    semantic_documents: list[SemanticDocumentManifest] = Field(min_length=6, max_length=6)

    @field_validator("spec_path")
    @classmethod
    def _spec_path(cls, value: str) -> str:
        return repo_relative_path(value)

    @model_validator(mode="after")
    def _bounded_inventory(self) -> PdfBenchmarkManifest:
        family_ids = [document.document_family_id for document in self.semantic_documents]
        semantic_ids = [document.semantic_document_id for document in self.semantic_documents]
        assets = [
            rendition for document in self.semantic_documents for rendition in document.renditions
        ]
        asset_ids = [rendition.asset_id for rendition in assets]
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("document families must be unique in the bounded benchmark")
        if len(semantic_ids) != len(set(semantic_ids)):
            raise ValueError("semantic document IDs must be unique")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("rendered asset IDs must be unique")
        measured = sum(asset.pdf_bytes for asset in assets)
        if self.total_pdf_bytes != measured:
            raise ValueError("total_pdf_bytes does not match rendition inventory")
        if measured > self.size_budget.max_total_pdf_bytes:
            raise ValueError("committed PDF inventory exceeds the total size budget")
        if any(asset.pdf_bytes > self.size_budget.max_single_pdf_bytes for asset in assets):
            raise ValueError("a rendered PDF exceeds the per-asset size budget")
        split_counts = {
            split: sum(document.split == split for document in self.semantic_documents)
            for split in BenchmarkSplit
        }
        if split_counts != {BenchmarkSplit.DEVELOPMENT: 3, BenchmarkSplit.HELD_OUT: 3}:
            raise ValueError("manifest must retain exactly three families in each split")
        return self


def load_pdf_benchmark_spec(path: Path) -> PdfBenchmarkSpec:
    """Load and strictly validate the hand-authored render specification."""
    return PdfBenchmarkSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_pdf_benchmark_manifest(path: Path) -> PdfBenchmarkManifest:
    """Load the runtime-safe rendition manifest; it cannot expose golden answers."""
    return PdfBenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))
