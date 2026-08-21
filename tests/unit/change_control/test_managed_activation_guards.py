"""Focused fail-closed guards for synchronous managed activation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mastervault.change_control.managed_activation_service import (
    ManagedActivationServiceError,
    _require_baseline_command_authority,
    _require_operator_generic_source,
)
from mastervault.change_control.managed_review import (
    GenerationZeroOriginBasis,
    GenericGoverningSourceAdoptionBindingV2,
    ManagedGoverningSourceAdoptionBinding,
)


def test_operator_activation_rejects_sealed_governing_source() -> None:
    sealed = ManagedGoverningSourceAdoptionBinding.model_construct()

    with pytest.raises(ManagedActivationServiceError, match="generic-v2"):
        _require_operator_generic_source(sealed)  # type: ignore[arg-type]


def test_generic_activation_rejects_missing_operator_run() -> None:
    generic = GenericGoverningSourceAdoptionBindingV2.model_construct()

    with pytest.raises(ManagedActivationServiceError, match="exact operator-run"):
        _require_operator_generic_source(generic, operator_run_present=False)


def test_operator_baseline_rejects_non_workspace_generation_zero_origin() -> None:
    record = SimpleNamespace(
        baseline_receipt=SimpleNamespace(
            authority=SimpleNamespace(query_generation=SimpleNamespace())
        )
    )
    expected_authority = SimpleNamespace(origin_basis=GenerationZeroOriginBasis.model_construct())

    with pytest.raises(ManagedActivationServiceError, match="workspace generation-zero"):
        _require_baseline_command_authority(
            record=record,  # type: ignore[arg-type]
            expected_authority=expected_authority,  # type: ignore[arg-type]
            embedding_model_version="mock-v1",
            embedding_dimensions=8,
        )
