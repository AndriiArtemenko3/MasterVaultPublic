from __future__ import annotations

import os
from pathlib import Path

import pytest

from mastervault.change_control.application_read_models import (
    ApplicationReadModelError,
    ApplicationReadModels,
    ApplicationReviewUnavailableError,
    _next_action,
    _outcome,
)
from mastervault.change_control.change_application_contracts import (
    ChangeRunNextActionV1,
    ChangeRunOutcomeV1,
    ChangeRunPhaseV1,
)
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunRecord,
    OperatorRunView,
)


def _run(*kinds: OperatorRunLinkKind) -> OperatorRunView:
    command = OperatorRunCommand.create(
        operation_id="read-model-test:run",
        aggregate_id="read-model-test",
        base_authority_id=f"mauthority:{'1' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="2" * 64,
    )
    links = tuple(
        OperatorRunLinkRecord(
            command=OperatorRunLinkCommand.create(
                operation_id=f"read-model-test:link-{sequence}",
                run_id=command.run_id,
                kind=kind,
                target_id=f"target:{sequence}",
                target_sha256=f"{sequence + 3:x}" * 64,
            ),
            sequence=sequence,
            recorded_at="2026-08-20T12:00:00+00:00",
        )
        for sequence, kind in enumerate(kinds)
    )
    return OperatorRunView(
        record=OperatorRunRecord(
            command=command,
            created_at="2026-08-20T12:00:00+00:00",
        ),
        links=links,
    )


@pytest.mark.parametrize(
    ("phase", "outcome", "next_action"),
    (
        (ChangeRunPhaseV1.BOOTSTRAPPED, ChangeRunOutcomeV1.IN_PROGRESS, ChangeRunNextActionV1.RESUME),
        (
            ChangeRunPhaseV1.AWAITING_TEMPORAL_REVIEW,
            ChangeRunOutcomeV1.IN_PROGRESS,
            ChangeRunNextActionV1.SUBMIT_TEMPORAL_REVIEW,
        ),
        (
            ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW,
            ChangeRunOutcomeV1.IN_PROGRESS,
            ChangeRunNextActionV1.SUBMIT_MANAGED_REVIEW,
        ),
        (
            ChangeRunPhaseV1.READY_TO_ACTIVATE,
            ChangeRunOutcomeV1.IN_PROGRESS,
            ChangeRunNextActionV1.ACTIVATE,
        ),
        (ChangeRunPhaseV1.ACTIVATED, ChangeRunOutcomeV1.ACTIVATED, ChangeRunNextActionV1.NONE),
        (
            ChangeRunPhaseV1.REJECTED_NO_OP,
            ChangeRunOutcomeV1.REJECTED_NO_OP,
            ChangeRunNextActionV1.NONE,
        ),
        (
            ChangeRunPhaseV1.COMPLETED_NO_OP,
            ChangeRunOutcomeV1.COMPLETED_NO_OP,
            ChangeRunNextActionV1.NONE,
        ),
    ),
)
def test_phase_projection_is_exact(
    phase: ChangeRunPhaseV1,
    outcome: ChangeRunOutcomeV1,
    next_action: ChangeRunNextActionV1,
) -> None:
    assert _outcome(phase) == outcome
    assert _next_action(phase) == next_action


def test_partial_restart_prefix_is_allowed_but_gap_fails_closed() -> None:
    base = (
        OperatorRunLinkKind.BOOTSTRAP_INTENT,
        OperatorRunLinkKind.WORKSPACE_INVENTORY,
        OperatorRunLinkKind.LEGACY_INDEX_READINESS,
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
    )
    ApplicationReadModels._require_shape(
        _run(*base, OperatorRunLinkKind.INCOMING_SOURCE),
        ChangeRunPhaseV1.BOOTSTRAPPED,
    )
    with pytest.raises(ApplicationReadModelError, match="evidence gap"):
        ApplicationReadModels._require_shape(
            _run(*base, OperatorRunLinkKind.REGRESSION_SUITE),
            ChangeRunPhaseV1.BOOTSTRAPPED,
        )


def test_surplus_link_fails_closed() -> None:
    base = (
        OperatorRunLinkKind.BOOTSTRAP_INTENT,
        OperatorRunLinkKind.WORKSPACE_INVENTORY,
        OperatorRunLinkKind.LEGACY_INDEX_READINESS,
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
    )
    with pytest.raises(ApplicationReadModelError, match="surplus"):
        ApplicationReadModels._require_shape(
            _run(*base, OperatorRunLinkKind.REPORT),
            ChangeRunPhaseV1.BOOTSTRAPPED,
        )


def test_constructor_is_read_only_and_creates_no_paths(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    os.chmod(evidence, 0o700)
    before = tuple(evidence.iterdir())
    ApplicationReadModels(tmp_path / "missing.sqlite3", evidence)
    assert tuple(evidence.iterdir()) == before
    assert not (tmp_path / "missing.sqlite3").exists()


def test_valid_non_review_phase_has_narrow_unavailable_error() -> None:
    models = object.__new__(ApplicationReadModels)
    run = _run(
        OperatorRunLinkKind.BOOTSTRAP_INTENT,
        OperatorRunLinkKind.WORKSPACE_INVENTORY,
        OperatorRunLinkKind.LEGACY_INDEX_READINESS,
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
    )
    models._read_run = lambda _run_id: (  # type: ignore[method-assign]  # noqa: SLF001
        run,
        ChangeRunPhaseV1.BOOTSTRAPPED,
    )

    with pytest.raises(ApplicationReviewUnavailableError):
        models.get_change_review(run.record.command.run_id)
