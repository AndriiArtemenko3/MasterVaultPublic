"""Versioned selection and result contracts for generation-aware queries.

The models in this module contain identities only.  They deliberately do not
know how authority is stored or how an index is opened; the application
facade owns that orchestration and returns :class:`ResolvedQueryGeneration`
with every required live guard attached.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mastervault.evidence import EvidenceLocation
from mastervault.storage.base import StorageBackend

_GENERATION_ID_PATTERN = r"^mgeneration:[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class QueryGenerationSelector(StrEnum):
    """Stable selector vocabulary for the v1 query-generation contract."""

    AUTO = "auto"
    LEGACY = "legacy"
    ACTIVE = "active"
    GENERATION_ID = "generation-id"


class QueryGenerationSelectionV1(_StrictFrozenModel):
    """One exact versioned query-generation request."""

    schema_version: Literal[1] = 1
    selector: QueryGenerationSelector
    generation_id: str | None = Field(default=None, pattern=_GENERATION_ID_PATTERN)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        exact = self.selector == QueryGenerationSelector.GENERATION_ID
        if exact != (self.generation_id is not None):
            raise ValueError("generation-id selection requires exactly one generation ID")
        return self

    @classmethod
    def parse(cls, value: str | QueryGenerationSelector) -> Self:
        """Parse the CLI spelling or an enum into one canonical request."""

        if isinstance(value, QueryGenerationSelector):
            if value == QueryGenerationSelector.GENERATION_ID:
                raise ValueError("generation-id selection requires an exact generation ID")
            return cls(selector=value)
        if not isinstance(value, str) or value != value.strip() or not value:
            raise ValueError("generation selection must be exact non-empty text")
        if value.startswith("mgeneration:"):
            return cls(selector=QueryGenerationSelector.GENERATION_ID, generation_id=value)
        try:
            selector = QueryGenerationSelector(value)
        except ValueError as exc:
            raise ValueError(
                "generation must be auto, legacy, active, or an exact mgeneration:<sha256>"
            ) from exc
        if selector == QueryGenerationSelector.GENERATION_ID:
            raise ValueError("generation-id selection requires an exact generation ID")
        return cls(selector=selector)


class QueryGenerationKind(StrEnum):
    """Kind of index that actually served a query."""

    UNMANAGED = "unmanaged-v0.2"
    GENERATION_ZERO = "generation-zero"
    MANAGED = "managed-generation"


class QueryGenerationMetadataV1(_StrictFrozenModel):
    """Stable, path-free identity of one resolved serving index."""

    schema_version: Literal[1] = 1
    selection: QueryGenerationSelectionV1
    backend: str
    generation_kind: QueryGenerationKind
    generation_id: str | None = Field(default=None, pattern=_GENERATION_ID_PATTERN)
    generation_number: int | None = Field(default=None, ge=0)
    active_generation_id: str | None = Field(default=None, pattern=_GENERATION_ID_PATTERN)
    active_authority_revision: int | None = Field(default=None, ge=0)
    is_active: bool
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    index_logical_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    index_file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    index_file_byte_count: int | None = Field(default=None, ge=1)
    storage_schema_version: int | None = Field(default=None, ge=1)
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _complete_identity(self) -> Self:
        managed_fields: tuple[Any, ...] = (
            self.generation_id,
            self.generation_number,
            self.active_generation_id,
            self.active_authority_revision,
            self.manifest_sha256,
            self.index_logical_fingerprint,
            self.index_file_sha256,
            self.index_file_byte_count,
            self.storage_schema_version,
            self.embedding_model,
            self.embedding_dimensions,
        )
        if self.generation_kind == QueryGenerationKind.UNMANAGED:
            if any(value is not None for value in managed_fields) or self.is_active:
                raise ValueError("unmanaged query metadata cannot claim managed authority")
            return self
        if any(value is None for value in managed_fields):
            raise ValueError("managed query metadata requires the complete index identity")
        assert self.generation_id is not None
        assert self.active_generation_id is not None
        if self.is_active != (self.generation_id == self.active_generation_id):
            raise ValueError("served-generation activity flag differs from active authority")
        if self.generation_kind == QueryGenerationKind.GENERATION_ZERO:
            if self.generation_number != 0:
                raise ValueError("generation-zero metadata must serve generation number zero")
        elif self.generation_number != 1:
            raise ValueError("the v0.3 managed query slice supports generation one only")
        return self

    @property
    def human_label(self) -> str:
        if self.generation_kind == QueryGenerationKind.UNMANAGED:
            return "unmanaged v0.2"
        assert self.generation_number is not None and self.generation_id is not None
        return f"{self.generation_number} · {self.generation_id} · verified"


@dataclass
class ResolvedQueryGeneration:
    """One read-only backend plus the guards that keep its resolution valid.

    Callers must use this object as a context manager (or call ``close``).
    Verification runs before output can be rendered and all resources are
    closed in deterministic backend-then-guard order.
    """

    backend: StorageBackend
    metadata: QueryGenerationMetadataV1
    evidence_workspaces: Mapping[str, Path | EvidenceLocation] = field(default_factory=dict)
    _verify_callbacks: tuple[Callable[[], None], ...] = field(default=(), repr=False)
    _verify_backend: Callable[[], None] | None = field(default=None, repr=False)
    _close_backend: Callable[[], None] | None = field(default=None, repr=False)
    _close_callbacks: tuple[Callable[[], None], ...] = field(default=(), repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> Self:
        try:
            self.verify()
        except BaseException as exc:
            self._release(exc, verify_callbacks=False)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    def verify(self) -> None:
        if self._closed:
            raise RuntimeError("resolved query generation is already closed")
        for callback in self._verify_callbacks:
            callback()

    def close(self) -> None:
        if self._closed:
            return
        self._release(None, verify_callbacks=True)

    def _release(
        self,
        failure: BaseException | None,
        *,
        verify_callbacks: bool,
    ) -> None:
        """Close everything once, retaining the first verification failure."""

        if self._closed:
            if failure is not None:
                raise failure
            return
        if self._verify_backend is not None:
            try:
                self._verify_backend()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if verify_callbacks:
            for callback in self._verify_callbacks:
                try:
                    callback()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
        try:
            (self._close_backend or self.backend.close)()
        except BaseException as exc:
            if failure is None:
                failure = exc
        for callback in self._close_callbacks:
            try:
                callback()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        self._closed = True
        if failure is not None:
            raise failure


__all__ = [
    "QueryGenerationKind",
    "QueryGenerationMetadataV1",
    "QueryGenerationSelectionV1",
    "QueryGenerationSelector",
    "ResolvedQueryGeneration",
]
