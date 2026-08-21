"""Strict admission contract for pre-activation regression suites."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from mastervault.change_control.models import (
    canonical_json_bytes,
    normalize_logical_key,
    normalize_semantic_text,
)

MAX_REGRESSION_SUITE_BYTES = 1024 * 1024
_FORBIDDEN_KEY_PARTS = frozenset(
    {"expected", "expect", "grade", "grading", "score", "patch", "review", "decision"}
)
_CASE_ID_PATTERN = r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"
_DOMAIN_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_RECORD_TYPES = frozenset({"claim", "chunk", "wiki", "structural"})


class RegressionSuiteError(ValueError):
    """A suite failed its bounded syntax or semantic contract."""


class RegressionSuiteBoundaryError(RegressionSuiteError):
    """Operator-supplied suite input is malformed or unsafe to admit."""


class RegressionSuiteIntegrityError(RegressionSuiteError):
    """Suite descriptors or bytes changed during their verified read."""


class RegressionSuiteUnsupportedError(RegressionSuiteError):
    """The host cannot provide the required no-follow admission guarantees."""


class RegressionCaseRole(StrEnum):
    TARGETED = "targeted"
    CONTROL = "control"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _normalized_text(value: str, *, label: str) -> str:
    normalized = normalize_semantic_text(value)
    if value != normalized:
        raise ValueError(f"{label} must already be normalized")
    return value


class _CommonRegressionCase(_StrictFrozenModel):
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    role: Literal["targeted", "control"]
    query: str = Field(min_length=1, max_length=4096)
    domain: str | None = Field(default=None, pattern=_DOMAIN_PATTERN)

    @field_validator("case_id")
    @classmethod
    def _case_id(cls, value: str) -> str:
        if normalize_logical_key(value) != value:
            raise ValueError("case_id must already be normalized")
        return value

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        return _normalized_text(value, label="query")


class SearchRegressionCaseV1(_CommonRegressionCase):
    kind: Literal["search"] = "search"
    k: int = Field(ge=1, le=100)
    record_types: tuple[str, ...] = Field(min_length=1, max_length=4)
    rerank: Literal[False] = False

    @field_validator("record_types")
    @classmethod
    def _record_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(value)) != value or len(set(value)) != len(value):
            raise ValueError("record_types must be sorted and unique")
        if not set(value) <= _RECORD_TYPES:
            raise ValueError("record_types contains an unsupported value")
        return value


class AskRegressionCaseV1(_CommonRegressionCase):
    kind: Literal["ask"] = "ask"
    max_rounds: int = Field(ge=1, le=20)
    budget_usd_micros: int = Field(ge=0, le=100_000_000)


RegressionCaseV1 = Annotated[
    SearchRegressionCaseV1 | AskRegressionCaseV1,
    Field(discriminator="kind"),
]


class RegressionSuiteV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    suite_id: str = Field(pattern=_CASE_ID_PATTERN)
    suite_version: int = Field(ge=1)
    cases: tuple[RegressionCaseV1, ...] = Field(min_length=1, max_length=128)

    @field_validator("suite_id")
    @classmethod
    def _suite_id(cls, value: str) -> str:
        if normalize_logical_key(value) != value:
            raise ValueError("suite_id must already be normalized")
        return value

    @model_validator(mode="before")
    @classmethod
    def _sort_cases(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("cases"), (list, tuple)):
            cases = value["cases"]
            if all(
                isinstance(item, dict) and isinstance(item.get("case_id"), str) for item in cases
            ):
                normalized = []
                for item in sorted(cases, key=lambda item: item["case_id"]):
                    if isinstance(item.get("record_types"), list):
                        item = {**item, "record_types": tuple(item["record_types"])}
                    normalized.append(item)
                return {**value, "cases": tuple(normalized)}
        return value

    @model_validator(mode="after")
    def _canonical_cases(self) -> Self:
        ids = tuple(item.case_id for item in self.cases)
        if len(set(ids)) != len(ids):
            raise ValueError("regression case IDs must be unique")
        if not any(item.role == RegressionCaseRole.TARGETED.value for item in self.cases):
            raise ValueError("regression suite requires at least one targeted case")
        if not any(item.role == RegressionCaseRole.CONTROL.value for item in self.cases):
            raise ValueError("regression suite requires at least one control case")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class AdmittedRegressionSuiteV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    suite: RegressionSuiteV1
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_byte_count: int = Field(ge=1, le=MAX_REGRESSION_SUITE_BYTES)
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.canonical_sha256 != self.suite.canonical_sha256:
            raise ValueError("canonical suite SHA-256 differs from canonical bytes")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RegressionSuiteBoundaryError(f"regression suite contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise RegressionSuiteBoundaryError(f"regression suite contains non-finite number {value}")


def _scan(value: object, *, depth: int = 0) -> None:
    if depth > 32:
        raise RegressionSuiteBoundaryError("regression suite exceeds maximum nesting depth")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = normalize_logical_key(str(key).replace("_", "-"))
            parts = frozenset(normalized.replace("-", ".").split("."))
            if parts & _FORBIDDEN_KEY_PARTS:
                raise RegressionSuiteBoundaryError(
                    f"regression suite contains forbidden answer-shaped key {key!r}"
                )
            _scan(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _scan(item, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise RegressionSuiteBoundaryError("regression suite contains a non-finite number")


def parse_regression_suite_bytes(payload: bytes) -> AdmittedRegressionSuiteV1:
    """Parse exact JSON bytes without coercion or ambiguous syntax."""

    if not payload or len(payload) > MAX_REGRESSION_SUITE_BYTES:
        raise RegressionSuiteBoundaryError("regression suite must be 1 byte to 1 MiB")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise RegressionSuiteBoundaryError("regression suite cannot contain a UTF-8 BOM")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegressionSuiteBoundaryError("regression suite must be UTF-8 JSON") from exc
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except RegressionSuiteBoundaryError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise RegressionSuiteBoundaryError("regression suite is not strict JSON") from exc
    if not isinstance(raw, dict):
        raise RegressionSuiteBoundaryError("regression suite root must be an object")
    _scan(raw)
    try:
        suite = TypeAdapter(RegressionSuiteV1).validate_python(raw, strict=True)
    except ValueError as exc:
        raise RegressionSuiteBoundaryError(f"regression suite is invalid: {exc}") from exc
    return AdmittedRegressionSuiteV1(
        suite=suite,
        original_sha256=hashlib.sha256(payload).hexdigest(),
        original_byte_count=len(payload),
        canonical_sha256=suite.canonical_sha256,
    )


def load_regression_suite(path: Path) -> AdmittedRegressionSuiteV1:
    """Read one owner-controlled regular file without following links."""

    if not isinstance(path, Path):
        raise TypeError("regression suite path must be pathlib.Path")
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RegressionSuiteUnsupportedError(
            "regression suite admission requires POSIX no-follow support"
        )
    absolute = path.absolute()
    parts = absolute.parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = -1
    child = -1
    fd = -1
    try:
        current = os.open(parts[0], directory_flags)
        for component in parts[1:-1]:
            names = os.listdir(current)
            if component not in names:
                raise RegressionSuiteBoundaryError(
                    "regression suite parent path changed or has wrong case"
                )
            child = os.open(component, directory_flags, dir_fd=current)
            child_info = os.fstat(child)
            named_info = os.stat(component, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(child_info.st_mode) or (
                child_info.st_dev,
                child_info.st_ino,
            ) != (named_info.st_dev, named_info.st_ino):
                raise RegressionSuiteIntegrityError("regression suite parent was substituted")
            os.close(current)
            current = child
            child = -1
        name = parts[-1]
        if name not in os.listdir(current):
            raise RegressionSuiteBoundaryError("regression suite file changed or has wrong case")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(name, flags, dir_fd=current)
        before = os.stat(name, dir_fd=current, follow_symlinks=False)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
        ):
            raise RegressionSuiteBoundaryError(
                "regression suite must be one owner-controlled regular file"
            )
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_REGRESSION_SUITE_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_REGRESSION_SUITE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
        finished = os.fstat(fd)
        after = os.stat(name, dir_fd=current, follow_symlinks=False)
    except OSError as exc:
        raise RegressionSuiteBoundaryError("regression suite cannot be opened safely") from exc
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
        if child >= 0:
            with suppress(OSError):
                os.close(child)
        if current >= 0:
            with suppress(OSError):
                os.close(current)
    signatures = {
        (
            item.st_dev,
            item.st_ino,
            item.st_uid,
            item.st_mode,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        for item in (before, opened, finished, after)
    }
    if len(signatures) != 1 or len(payload) != after.st_size:
        raise RegressionSuiteIntegrityError("regression suite changed during admission")
    return parse_regression_suite_bytes(payload)


__all__ = [
    "AdmittedRegressionSuiteV1",
    "AskRegressionCaseV1",
    "MAX_REGRESSION_SUITE_BYTES",
    "RegressionCaseRole",
    "RegressionCaseV1",
    "RegressionSuiteError",
    "RegressionSuiteBoundaryError",
    "RegressionSuiteIntegrityError",
    "RegressionSuiteUnsupportedError",
    "RegressionSuiteV1",
    "SearchRegressionCaseV1",
    "load_regression_suite",
    "parse_regression_suite_bytes",
]
