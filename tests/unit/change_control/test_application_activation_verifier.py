from __future__ import annotations

from types import SimpleNamespace

import pytest

from mastervault.change_control.application_activation_verifier import (
    ActivatedEvidenceVerificationError,
    ReadOnlyActivatedEvidenceVerifier,
)
from mastervault.change_control.change_application_contracts import (
    AuthoritySummaryV1,
    ChangeActivationEvidenceSummaryV1,
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
)
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunRecord,
    OperatorRunView,
)
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationMetadataV1,
    QueryGenerationSelectionV1,
    QueryGenerationSelector,
    ResolvedQueryGeneration,
)


def _run_and_status() -> tuple[OperatorRunView, ChangeRunStatusV1]:
    command = OperatorRunCommand.create(
        operation_id="activated-verifier:run",
        aggregate_id="activated-verifier",
        base_authority_id=f"mauthority:{'1' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="2" * 64,
    )
    run = OperatorRunView(
        record=OperatorRunRecord(
            command=command,
            created_at="2026-08-20T12:00:00+00:00",
        ),
        links=(),
    )
    current = AuthoritySummaryV1(
        authority_id=f"mauthority:{'3' * 64}",
        revision=1,
        generation_id=f"mgeneration:{'4' * 64}",
        generation_number=1,
        manifest_sha256="5" * 64,
        active_pointer_sha256="6" * 64,
        is_active=True,
    )
    status = ChangeRunStatusV1.model_construct(
        run_id=command.run_id,
        phase=ChangeRunPhaseV1.ACTIVATED,
        current_authority=current,
        activation=ChangeActivationEvidenceSummaryV1(
            receipt_id=f"mgenerationactivation:{'7' * 64}",
            receipt_sha256="7" * 64,
            generation_id=current.generation_id,
        ),
    )
    return run, status


def _resolved(
    *,
    verified: list[str],
    closed: list[str],
    manifest_sha256: str = "5" * 64,
    verify_error: BaseException | None = None,
) -> ResolvedQueryGeneration:
    metadata = QueryGenerationMetadataV1(
        selection=QueryGenerationSelectionV1(selector=QueryGenerationSelector.ACTIVE),
        backend="sqlite",
        generation_kind=QueryGenerationKind.MANAGED,
        generation_id=f"mgeneration:{'4' * 64}",
        generation_number=1,
        active_generation_id=f"mgeneration:{'4' * 64}",
        active_authority_revision=1,
        is_active=True,
        manifest_sha256=manifest_sha256,
        index_logical_fingerprint="8" * 64,
        index_file_sha256="9" * 64,
        index_file_byte_count=1,
        storage_schema_version=1,
        embedding_model="test-embedding",
        embedding_dimensions=1,
    )

    def verify() -> None:
        verified.append("verify")
        if verify_error is not None:
            raise verify_error

    def close() -> None:
        closed.append("close")

    return ResolvedQueryGeneration(
        backend=SimpleNamespace(close=close),  # type: ignore[arg-type]
        metadata=metadata,
        _verify_callbacks=(verify,),
        _close_backend=close,
    )


def test_activated_verifier_verifies_and_closes_exact_active_generation() -> None:
    run, status = _run_and_status()
    verified: list[str] = []
    closed: list[str] = []
    verifier = ReadOnlyActivatedEvidenceVerifier(
        lambda: _resolved(verified=verified, closed=closed)
    )

    verifier(run, status)

    assert verified == ["verify", "verify"]
    assert closed == ["close"]


def test_activated_verifier_closes_when_metadata_is_cross_generation() -> None:
    run, status = _run_and_status()
    verified: list[str] = []
    closed: list[str] = []
    verifier = ReadOnlyActivatedEvidenceVerifier(
        lambda: _resolved(
            verified=verified,
            closed=closed,
            manifest_sha256="a" * 64,
        )
    )

    with pytest.raises(ActivatedEvidenceVerificationError):
        verifier(run, status)

    assert closed == ["close"]


def test_activated_verifier_closes_when_serving_verification_fails() -> None:
    run, status = _run_and_status()
    verified: list[str] = []
    closed: list[str] = []
    verifier = ReadOnlyActivatedEvidenceVerifier(
        lambda: _resolved(
            verified=verified,
            closed=closed,
            verify_error=RuntimeError("tampered index"),
        )
    )

    with pytest.raises(RuntimeError, match="tampered index"):
        verifier(run, status)

    assert closed == ["close"]
