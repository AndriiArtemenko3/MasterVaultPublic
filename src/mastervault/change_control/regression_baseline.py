"""Buffered generation-zero regression execution and immutable evidence repository."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mastervault.change_control.inference_repository import (
    FilesystemInferenceEvidenceRepository,
    InferenceEvidenceRepositoryError,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    ResolvedQueryGeneration,
)
from mastervault.change_control.regression_suite import (
    AdmittedRegressionSuiteV1,
    AskRegressionCaseV1,
    SearchRegressionCaseV1,
)
from mastervault.config import Settings
from mastervault.models import Hit
from mastervault.pipelines.ask import run_ask
from mastervault.prompts.registry import load as load_prompt
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.providers.llm import LLMProvider
from mastervault.providers.reranker import Reranker
from mastervault.retrieval.search import hybrid_search

_SHA_PATTERN = r"^[0-9a-f]{64}$"
_IDENTITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$"
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_CAPABILITY_SECRET = os.urandom(32)


class RegressionBaselineError(RuntimeError):
    """Baseline evidence is incomplete, unsafe, changed, or mismatched."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if (
        offset is None
        or offset.total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("captured_at must be canonical UTC with second precision")
    return value


def _micros(value: float) -> int:
    return int(
        (Decimal(str(value)) * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    )


class RegressionRuntimeIdentityV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    retrieval_k: int = Field(ge=1)
    retrieval_rrf_k: int = Field(ge=1)
    retrieval_rerank_pool: int = Field(ge=1)
    retrieval_mmr_lambda: float = Field(ge=0.0, le=1.0)
    embedding_provider: str = Field(min_length=1, max_length=128)
    embedding_model: str = Field(min_length=1, max_length=512)
    embedding_dimensions: int = Field(ge=1)
    llm_provider: str = Field(min_length=1, max_length=128)
    llm_implementation: str = Field(min_length=1, max_length=512)
    llm_model_small: str = Field(min_length=1, max_length=512)
    llm_model_medium: str = Field(min_length=1, max_length=512)
    llm_model_large: str = Field(min_length=1, max_length=512)
    prompt_sha256: Mapping[str, str]
    response_schema_sha256: Mapping[str, str]

    @model_validator(mode="after")
    def _hashes(self) -> Self:
        expected = {"grounded_synthesis.v1", "sufficiency_judge.v1"}
        if set(self.prompt_sha256) != expected or set(self.response_schema_sha256) != expected:
            raise ValueError("ask prompt/schema identity must be complete")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in (*self.prompt_sha256.values(), *self.response_schema_sha256.values())
        ):
            raise ValueError("ask prompt/schema SHA-256 is invalid")
        return self


class RegressionAuthorityBindingV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY_PATTERN)
    source_admission_id: str = Field(pattern=_IDENTITY_PATTERN)
    source_admission_sha256: str = Field(pattern=_SHA_PATTERN)
    workspace_inventory_id: str = Field(pattern=_IDENTITY_PATTERN)
    workspace_inventory_sha256: str = Field(pattern=_SHA_PATTERN)
    legacy_readiness_id: str = Field(pattern=_IDENTITY_PATTERN)
    legacy_readiness_sha256: str = Field(pattern=_SHA_PATTERN)
    query_generation: QueryGenerationMetadataV1

    @model_validator(mode="after")
    def _generation_zero(self) -> Self:
        metadata = self.query_generation
        if not (
            metadata.generation_kind == QueryGenerationKind.GENERATION_ZERO
            and metadata.generation_number == 0
            and metadata.active_authority_revision == 0
            and metadata.is_active
        ):
            raise ValueError("baseline authority must be the active generation zero revision")
        return self


class BufferedRegressionCaseV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    case_id: str
    case_kind: Literal["search", "ask"]
    payload_sha256: str = Field(pattern=_SHA_PATTERN)
    payload_byte_count: int = Field(ge=1, le=_MAX_ARTIFACT_BYTES)
    payload: dict[str, Any] = Field(exclude=True)

    @model_validator(mode="after")
    def _payload_identity(self) -> Self:
        payload = canonical_json_bytes(self.payload)
        if self.payload_sha256 != _sha(payload) or self.payload_byte_count != len(payload):
            raise ValueError("buffered case identity differs from canonical payload")
        return self

    @classmethod
    def create(
        cls, *, case_id: str, case_kind: Literal["search", "ask"], payload: dict[str, Any]
    ) -> Self:
        snapshot = json.loads(canonical_json_bytes(payload))
        encoded = canonical_json_bytes(snapshot)
        if len(encoded) > _MAX_ARTIFACT_BYTES:
            raise RegressionBaselineError("baseline case exceeds fixed artifact limit")
        return cls(
            case_id=case_id,
            case_kind=case_kind,
            payload_sha256=_sha(encoded),
            payload_byte_count=len(encoded),
            payload=snapshot,
        )


class PreparedGenerationZeroBaselineV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    authority: RegressionAuthorityBindingV1
    suite: AdmittedRegressionSuiteV1
    runtime: RegressionRuntimeIdentityV1
    cases: tuple[BufferedRegressionCaseV1, ...]

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        expected = tuple(item.case_id for item in self.suite.suite.cases)
        actual = tuple(item.case_id for item in self.cases)
        if actual != expected:
            raise ValueError("buffered baseline does not exactly cover suite query inventory")
        for spec, result in zip(self.suite.suite.cases, self.cases, strict=True):
            if spec.kind != result.case_kind:
                raise ValueError("buffered baseline case kind differs from suite")
        return self

    @property
    def baseline_id(self) -> str:
        payload = {
            "schema_version": 1,
            "authority": self.authority.model_dump(mode="json"),
            "suite_id": self.suite.suite.suite_id,
            "suite_version": self.suite.suite.suite_version,
            "suite_original_sha256": self.suite.original_sha256,
            "suite_canonical_sha256": self.suite.canonical_sha256,
            "runtime": self.runtime.model_dump(mode="json"),
            "cases": [
                {
                    "case_id": item.case_id,
                    "case_kind": item.case_kind,
                    "payload_sha256": item.payload_sha256,
                    "payload_byte_count": item.payload_byte_count,
                }
                for item in self.cases
            ],
        }
        return f"regbaseline:{_sha(canonical_json_bytes(payload))}"


class RegressionBaselineArtifactV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    case_id: str
    case_kind: Literal["search", "ask"]
    relative_path: str
    sha256: str = Field(pattern=_SHA_PATTERN)
    byte_count: int = Field(ge=1, le=_MAX_ARTIFACT_BYTES)

    @field_validator("relative_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or any(part.startswith(".") for part in path.parts)
        ):
            raise ValueError("baseline artifact locator is unsafe")
        return value


class GenerationZeroBaselineReceiptV1(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(pattern=r"^regreceipt:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=_SHA_PATTERN)
    baseline_id: str = Field(pattern=r"^regbaseline:[0-9a-f]{64}$")
    authority: RegressionAuthorityBindingV1
    suite_id: str
    suite_version: int = Field(ge=1)
    suite_original_sha256: str = Field(pattern=_SHA_PATTERN)
    suite_canonical_sha256: str = Field(pattern=_SHA_PATTERN)
    runtime: RegressionRuntimeIdentityV1
    query_inventory: tuple[str, ...] = Field(min_length=1, max_length=128)
    artifacts: tuple[RegressionBaselineArtifactV1, ...] = Field(min_length=1, max_length=128)
    captured_at: str

    @field_validator("captured_at")
    @classmethod
    def _timestamp(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.query_inventory != tuple(item.case_id for item in self.artifacts):
            raise ValueError("baseline receipt inventory differs from artifacts")
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})
        digest = _sha(canonical_json_bytes(payload))
        if self.receipt_sha256 != digest or self.receipt_id != f"regreceipt:{digest}":
            raise ValueError("baseline receipt identity differs from canonical evidence")
        return self

    @classmethod
    def create(
        cls,
        prepared: PreparedGenerationZeroBaselineV1,
        *,
        artifacts: tuple[RegressionBaselineArtifactV1, ...],
        captured_at: str,
    ) -> Self:
        canonical_captured_at = _canonical_utc(captured_at)
        identity = {
            "schema_version": 1,
            "baseline_id": prepared.baseline_id,
            "authority": prepared.authority.model_dump(mode="json"),
            "suite_id": prepared.suite.suite.suite_id,
            "suite_version": prepared.suite.suite.suite_version,
            "suite_original_sha256": prepared.suite.original_sha256,
            "suite_canonical_sha256": prepared.suite.canonical_sha256,
            "runtime": prepared.runtime.model_dump(mode="json"),
            "query_inventory": [item.case_id for item in prepared.cases],
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "captured_at": canonical_captured_at,
        }
        digest = _sha(canonical_json_bytes(identity))
        return cls(
            receipt_id=f"regreceipt:{digest}",
            receipt_sha256=digest,
            baseline_id=prepared.baseline_id,
            authority=prepared.authority,
            suite_id=prepared.suite.suite.suite_id,
            suite_version=prepared.suite.suite.suite_version,
            suite_original_sha256=prepared.suite.original_sha256,
            suite_canonical_sha256=prepared.suite.canonical_sha256,
            runtime=prepared.runtime,
            query_inventory=tuple(item.case_id for item in prepared.cases),
            artifacts=artifacts,
            captured_at=canonical_captured_at,
        )


@dataclass(frozen=True, slots=True)
class VerifiedGenerationZeroBaselineCapability:
    receipt: GenerationZeroBaselineReceiptV1
    repository_id: str
    _seal: bytes = field(repr=False)


def _hit_payload(hit: Hit) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _sanitize_projection(
            {
                "record_id": hit.record_id,
                "record_type": hit.record_type.value,
                "doc_id": hit.doc_id,
                "domain": hit.domain.value,
                "text": hit.text,
                "rel_path": _safe_locator(hit.rel_path),
                "confidence": hit.confidence.value if hit.confidence is not None else None,
                "evidence": [item.model_dump(mode="json") for item in hit.evidence],
                "structural_kind": hit.structural_kind,
                "source_identity": hit.source_identity,
                "channels": hit.channels.model_dump(mode="json"),
                "rrf_score": hit.rrf_score,
                "rerank_score": hit.rerank_score,
            }
        ),
    )


def _safe_locator(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegressionBaselineError("citation locator must be text")
    path = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or "\\" in value
    ):
        raise RegressionBaselineError("citation locator must be safe repository-relative text")
    return value


def _sanitize_projection(value: Any, *, key: str = "") -> Any:
    folded = key.casefold().replace("-", "_")
    if any(token in folded for token in ("secret", "api_key", "password", "credential")):
        raise RegressionBaselineError("baseline projection contains secret-shaped metadata")
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_projection(child, key=str(child_key))
            for child_key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_sanitize_projection(item, key=key) for item in value]
    if isinstance(value, str):
        if value.startswith(("/", "\\")) or (len(value) >= 3 and value[1:3] in {":/", ":\\"}):
            raise RegressionBaselineError("baseline projection contains an absolute path")
        if "path" in folded or "locator" in folded or folded == "rel_path":
            return _safe_locator(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise RegressionBaselineError("baseline projection contains a non-JSON value")


def _ask_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "record_id",
        "rel_path",
        "text",
        "record_type",
        "domain",
        "evidence",
        "source_identity",
    )
    return cast(
        dict[str, Any],
        _sanitize_projection({key: value[key] for key in allowed if key in value}),
    )


def _prompt_identities() -> tuple[dict[str, str], dict[str, str]]:
    prompts: dict[str, str] = {}
    schemas: dict[str, str] = {}
    for contract_id in ("grounded_synthesis", "sufficiency_judge"):
        spec = load_prompt(contract_id, 1)
        key = f"{contract_id}.v1"
        prompt_file = resources.files("mastervault.prompts") / contract_id / "v1.md"
        prompts[key] = _sha(prompt_file.read_bytes())
        schemas[key] = _sha(canonical_json_bytes(spec.output_model.model_json_schema()))
    return prompts, schemas


def regression_runtime_identity(
    settings: Settings,
    *,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
) -> RegressionRuntimeIdentityV1:
    prompt_hashes, schema_hashes = _prompt_identities()
    return RegressionRuntimeIdentityV1(
        retrieval_k=settings.retrieval.k,
        retrieval_rrf_k=settings.retrieval.rrf_k,
        retrieval_rerank_pool=settings.retrieval.rerank_pool,
        retrieval_mmr_lambda=settings.retrieval.mmr_lambda,
        embedding_provider=embedder.name,
        embedding_model=embedder.model_version,
        embedding_dimensions=embedder.dimensions,
        llm_provider=settings.llm.provider,
        llm_implementation=f"{type(llm).__module__}.{type(llm).__qualname__}",
        llm_model_small=settings.llm.model_small,
        llm_model_medium=settings.llm.model_medium,
        llm_model_large=settings.llm.model_large,
        prompt_sha256=prompt_hashes,
        response_schema_sha256=schema_hashes,
    )


def execute_generation_zero_baseline(
    *,
    resolved: ResolvedQueryGeneration,
    authority: RegressionAuthorityBindingV1,
    suite: AdmittedRegressionSuiteV1,
    settings: Settings,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    reranker: Reranker | None = None,
) -> PreparedGenerationZeroBaselineV1:
    """Execute and buffer exact evidence without closing or mutating caller resources."""

    if resolved.metadata != authority.query_generation:
        raise RegressionBaselineError("resolved generation differs from baseline authority")
    if reranker is not None:
        raise RegressionBaselineError("regression suite v1 forbids reranking")
    runtime = regression_runtime_identity(settings, embedder=embedder, llm=llm)
    if (
        runtime.embedding_model != resolved.metadata.embedding_model
        or runtime.embedding_dimensions != resolved.metadata.embedding_dimensions
    ):
        raise RegressionBaselineError("embedding runtime differs from generation-zero index")
    resolved.verify()
    buffered: list[BufferedRegressionCaseV1] = []
    for case in suite.suite.cases:
        if isinstance(case, SearchRegressionCaseV1):
            result = hybrid_search(
                case.query,
                settings,
                resolved.backend,
                embedder,
                reranker,
                k=case.k,
                domain=case.domain,
                record_types=list(case.record_types),
                rerank=False,
                evidence_workspaces=resolved.evidence_workspaces or None,
            )
            payload = {
                "schema_version": 1,
                "case_id": case.case_id,
                "kind": "search",
                "query": case.query,
                "domain": case.domain,
                "k": case.k,
                "record_types": list(case.record_types),
                "rerank": False,
                "generation": resolved.metadata.model_dump(mode="json"),
                "wiki_card": _hit_payload(result.wiki_card) if result.wiki_card else None,
                "hits": [_hit_payload(hit) for hit in result.hits],
                "channel_counts": dict(sorted(result.channel_counts.items())),
            }
        elif isinstance(case, AskRegressionCaseV1):
            outcome = run_ask(
                case.query,
                settings,
                resolved.backend,
                embedder,
                llm,
                domain=case.domain,
                max_rounds=case.max_rounds,
                budget_usd=case.budget_usd_micros / 1_000_000,
                evidence_workspaces=resolved.evidence_workspaces or None,
                persist_run=False,
            )
            payload = {
                "schema_version": 1,
                "case_id": case.case_id,
                "kind": "ask",
                "query": case.query,
                "domain": case.domain,
                "max_rounds": case.max_rounds,
                "budget_usd_micros": case.budget_usd_micros,
                "generation": resolved.metadata.model_dump(mode="json"),
                "answer_markdown": outcome.answer_markdown,
                "confidence": outcome.confidence,
                "gaps": outcome.gaps,
                "sources": [_ask_reference(item) for item in outcome.sources],
                "evidence": [_ask_reference(item) for item in outcome.evidence],
                "warnings": outcome.warnings,
                "extractive": outcome.extractive,
                "zero_evidence": outcome.zero_evidence,
                "rounds": outcome.rounds,
                "cost_usd_micros": _micros(outcome.cost_usd),
                "nearest_wiki_titles": outcome.nearest_wiki_titles,
                "exit_code": outcome.exit_code,
            }
        else:  # pragma: no cover - discriminated union guard
            raise TypeError("unsupported regression case")
        buffered.append(
            BufferedRegressionCaseV1.create(
                case_id=case.case_id,
                case_kind=case.kind,
                payload=payload,
            )
        )
    resolved.verify()
    return PreparedGenerationZeroBaselineV1(
        authority=authority,
        suite=suite,
        runtime=runtime,
        cases=tuple(buffered),
    )


class GenerationZeroBaselineRepository:
    """Create-only owner-private evidence; COMPLETE is the sole repository seal."""

    def __init__(self, root: Path):
        if not isinstance(root, Path):
            raise TypeError("baseline repository root must be pathlib.Path")
        try:
            self._repository = FilesystemInferenceEvidenceRepository(root)
        except InferenceEvidenceRepositoryError as exc:
            raise RegressionBaselineError(
                "baseline repository cannot be established safely"
            ) from exc
        self.root = self._repository.root
        self.repository_id = self._repository.repository_id

    @staticmethod
    def _run_name(run_id: str) -> str:
        return _sha(run_id.encode("utf-8"))

    def _relative(self, run_id: str, suffix: str) -> str:
        return f"regression-baselines/runs/{self._run_name(run_id)}/{suffix}"

    def _read_required(self, relative: str, *, limit: int, label: str) -> bytes:
        payload = self._repository._read_optional(  # noqa: SLF001
            relative,
            limit=limit,
            label=label,
        )
        if payload is None:
            raise RegressionBaselineError(f"{label} is missing")
        return payload

    def _require_exact_inventory(
        self,
        run_id: str,
        *,
        receipt: GenerationZeroBaselineReceiptV1,
    ) -> None:
        expected_root = {"SUITE.json", "COMPLETE.json", "cases"}
        run_placeholder = self._relative(run_id, "placeholder")
        cases_placeholder = self._relative(run_id, "cases/placeholder")
        run_fd = cases_fd = -1
        try:
            run_fd, _ = self._repository._open_parent(  # noqa: SLF001
                run_placeholder, create=False
            )
            cases_fd, _ = self._repository._open_parent(  # noqa: SLF001
                cases_placeholder, create=False
            )
            if set(os.listdir(run_fd)) != expected_root:
                raise RegressionBaselineError("baseline run has surplus or missing artifacts")
            expected_cases = {f"{item.case_id}.json" for item in receipt.artifacts}
            if set(os.listdir(cases_fd)) != expected_cases:
                raise RegressionBaselineError("baseline case inventory is not exact")
        except FileNotFoundError as exc:
            raise RegressionBaselineError("baseline evidence directory is incomplete") from exc
        finally:
            if cases_fd >= 0:
                os.close(cases_fd)
            if run_fd >= 0:
                os.close(run_fd)

    def publish(
        self,
        prepared: PreparedGenerationZeroBaselineV1,
        *,
        captured_at: str,
        failure_hook: Any | None = None,
    ) -> VerifiedGenerationZeroBaselineCapability:
        """Publish buffered evidence and seal it last; exact partial replay converges."""

        try:
            with self._repository._exclusive_lock():  # noqa: SLF001
                complete_relative = self._relative(prepared.authority.run_id, "COMPLETE.json")
                existing = self._repository._read_optional(  # noqa: SLF001
                    complete_relative,
                    limit=_MAX_ARTIFACT_BYTES,
                    label="baseline COMPLETE receipt",
                )
                if existing is not None:
                    receipt = self._open_locked(prepared.authority.run_id, existing)
                    self._require_receipt_matches(receipt, prepared)
                    return self._capability(receipt)

                suite_relative = self._relative(prepared.authority.run_id, "SUITE.json")
                self._repository._create_only(  # noqa: SLF001
                    suite_relative,
                    prepared.suite.suite.canonical_bytes,
                    label="canonical regression suite",
                )
                if failure_hook is not None:
                    failure_hook("suite-published")
                artifacts: list[RegressionBaselineArtifactV1] = []
                for item in prepared.cases:
                    payload = canonical_json_bytes(item.payload)
                    relative = self._relative(
                        prepared.authority.run_id, f"cases/{item.case_id}.json"
                    )
                    self._repository._create_only(  # noqa: SLF001
                        relative, payload, label="regression baseline case"
                    )
                    if failure_hook is not None:
                        failure_hook(f"case-published:{item.case_id}")
                    artifacts.append(
                        RegressionBaselineArtifactV1(
                            case_id=item.case_id,
                            case_kind=item.case_kind,
                            relative_path=relative,
                            sha256=item.payload_sha256,
                            byte_count=item.payload_byte_count,
                        )
                    )
                receipt = GenerationZeroBaselineReceiptV1.create(
                    prepared,
                    artifacts=tuple(artifacts),
                    captured_at=captured_at,
                )
                self._repository._create_only(  # noqa: SLF001
                    complete_relative,
                    canonical_json_bytes(receipt.model_dump(mode="json")),
                    label="baseline COMPLETE receipt",
                )
                if failure_hook is not None:
                    failure_hook("complete-published")
                reopened = self._open_locked(
                    prepared.authority.run_id,
                    self._read_required(
                        complete_relative,
                        limit=_MAX_ARTIFACT_BYTES,
                        label="baseline COMPLETE receipt",
                    ),
                )
                self._require_receipt_matches(reopened, prepared)
                return self._capability(reopened)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, RegressionBaselineError):
                raise
            raise RegressionBaselineError("baseline publication failed closed") from exc

    def _require_receipt_matches(
        self,
        receipt: GenerationZeroBaselineReceiptV1,
        prepared: PreparedGenerationZeroBaselineV1,
    ) -> None:
        if not (
            receipt.baseline_id == prepared.baseline_id
            and receipt.authority == prepared.authority
            and receipt.suite_original_sha256 == prepared.suite.original_sha256
            and receipt.suite_canonical_sha256 == prepared.suite.canonical_sha256
            and receipt.runtime == prepared.runtime
            and tuple(item.sha256 for item in receipt.artifacts)
            == tuple(item.payload_sha256 for item in prepared.cases)
        ):
            raise RegressionBaselineError("run baseline already exists for different inputs")

    def open(self, run_id: str) -> GenerationZeroBaselineReceiptV1:
        try:
            with self._repository._read_lock():  # noqa: SLF001
                complete = self._read_required(
                    self._relative(run_id, "COMPLETE.json"),
                    limit=_MAX_ARTIFACT_BYTES,
                    label="baseline COMPLETE receipt",
                )
                return self._open_locked(run_id, complete)
        except (InferenceEvidenceRepositoryError, OSError, ValueError) as exc:
            if isinstance(exc, RegressionBaselineError):
                raise
            raise RegressionBaselineError("baseline reopen failed closed") from exc

    def _open_locked(self, run_id: str, complete: bytes) -> GenerationZeroBaselineReceiptV1:
        try:
            receipt = GenerationZeroBaselineReceiptV1.model_validate_json(complete, strict=True)
        except ValueError as exc:
            raise RegressionBaselineError("baseline COMPLETE receipt is invalid") from exc
        if receipt.authority.run_id != run_id:
            raise RegressionBaselineError("baseline COMPLETE belongs to another run")
        self._require_exact_inventory(run_id, receipt=receipt)
        suite = self._read_required(
            self._relative(run_id, "SUITE.json"),
            limit=_MAX_ARTIFACT_BYTES,
            label="canonical regression suite",
        )
        if _sha(suite) != receipt.suite_canonical_sha256:
            raise RegressionBaselineError("baseline suite artifact was altered")
        for artifact in receipt.artifacts:
            expected_prefix = self._relative(run_id, "cases/")
            if not artifact.relative_path.startswith(expected_prefix):
                raise RegressionBaselineError("baseline artifact locator escapes its run")
            payload = self._read_required(
                artifact.relative_path,
                limit=artifact.byte_count,
                label="regression baseline case",
            )
            if _sha(payload) != artifact.sha256 or len(payload) != artifact.byte_count:
                raise RegressionBaselineError("baseline case artifact was altered")
        return receipt

    def _capability(
        self, receipt: GenerationZeroBaselineReceiptV1
    ) -> VerifiedGenerationZeroBaselineCapability:
        seal = hmac.digest(
            _CAPABILITY_SECRET,
            canonical_json_bytes(
                {
                    "repository_id": self.repository_id,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.receipt_sha256,
                }
            ),
            "sha256",
        )
        return VerifiedGenerationZeroBaselineCapability(receipt, self.repository_id, seal)

    def verify_capability(
        self, capability: VerifiedGenerationZeroBaselineCapability
    ) -> GenerationZeroBaselineReceiptV1:
        receipt = self.open(capability.receipt.authority.run_id)
        expected = self._capability(receipt)
        if (
            capability.repository_id != self.repository_id
            or capability.receipt != receipt
            or not hmac.compare_digest(capability._seal, expected._seal)
        ):
            raise RegressionBaselineError("baseline capability is invalid or belongs elsewhere")
        return receipt


__all__ = [
    "BufferedRegressionCaseV1",
    "GenerationZeroBaselineReceiptV1",
    "GenerationZeroBaselineRepository",
    "PreparedGenerationZeroBaselineV1",
    "RegressionAuthorityBindingV1",
    "RegressionBaselineArtifactV1",
    "RegressionBaselineError",
    "RegressionRuntimeIdentityV1",
    "VerifiedGenerationZeroBaselineCapability",
    "execute_generation_zero_baseline",
    "regression_runtime_identity",
]
