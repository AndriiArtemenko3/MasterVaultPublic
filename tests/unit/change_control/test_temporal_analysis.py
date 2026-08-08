from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from test_temporal_proposal import _build_case, _Case

from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.temporal_analysis import (
    MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1,
    TemporalAnalysisEvidence,
    build_temporal_analysis_evidence,
    verify_temporal_analysis_evidence,
)


@pytest.fixture(scope="module")
def temporal_case(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Case]:
    case = _build_case(tmp_path_factory.mktemp("temporal-analysis"))
    yield case
    case.store.close()


@pytest.fixture(scope="module")
def temporal_evidence(temporal_case: _Case) -> TemporalAnalysisEvidence:
    return _evidence(temporal_case)


def _evidence(case: _Case) -> TemporalAnalysisEvidence:
    values = case.build_inputs
    return build_temporal_analysis_evidence(
        verified_bootstrap=values["verified_bootstrap"],
        snapshot=values["snapshot"],
        candidates=values["candidates"],
        classification_results=values["classification_results"],
        inventory_capability=values["inventory_capability"],
        dependency_workload=values["dependency_workload"],
        dependency_results=values["dependency_results"],
        replacement_candidate=values["replacement_candidate"],
        proposal=case.proposal,
    )


def _verify(case: _Case, evidence: TemporalAnalysisEvidence):
    values = case.build_inputs
    return verify_temporal_analysis_evidence(
        evidence,
        verified_bootstrap=values["verified_bootstrap"],
        inventory_capability=values["inventory_capability"],
        classification_outcomes=values["classification_outcomes"],
        dependency_outcomes=values["dependency_outcomes"],
    )


def _tampered_bytes(
    evidence: TemporalAnalysisEvidence,
    mutate: Callable[[dict[str, Any]], None],
) -> bytes:
    payload = json.loads(evidence.canonical_bytes())
    assert isinstance(payload, dict)
    mutate(payload)
    return canonical_json_bytes(payload)


def test_real_sl2_manifest_round_trip_reproduces_exact_proposal(
    temporal_case: _Case,
    temporal_evidence: TemporalAnalysisEvidence,
) -> None:
    persisted = temporal_evidence.canonical_bytes()
    assert len(persisted) <= MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1
    assert "manifest_id" not in json.loads(persisted)
    assert "manifest_sha256" not in json.loads(persisted)

    reopened = TemporalAnalysisEvidence.from_canonical_bytes(persisted)
    verified = _verify(temporal_case, reopened)

    assert reopened == temporal_evidence
    assert verified == temporal_case.proposal
    assert reopened.manifest_id == f"temporal-analysis:{reopened.manifest_sha256}"
    assert reopened.classification_result_index.output_shards
    assert reopened.dependency_result_index.output_shards


def test_restart_verification_rejects_missing_and_substituted_output_shards(
    temporal_case: _Case,
    temporal_evidence: TemporalAnalysisEvidence,
) -> None:
    values = temporal_case.build_inputs
    classification_outcomes = values["classification_outcomes"]
    with pytest.raises(ValueError):
        verify_temporal_analysis_evidence(
            temporal_evidence,
            verified_bootstrap=values["verified_bootstrap"],
            inventory_capability=values["inventory_capability"],
            classification_outcomes=classification_outcomes[:-1],
            dependency_outcomes=values["dependency_outcomes"],
        )

    substituted = classification_outcomes[0].model_copy(
        update={
            "classification_output": classification_outcomes[1].classification_output,
        }
    )
    with pytest.raises(ValueError):
        verify_temporal_analysis_evidence(
            temporal_evidence,
            verified_bootstrap=values["verified_bootstrap"],
            inventory_capability=values["inventory_capability"],
            classification_outcomes=(substituted, *classification_outcomes[1:]),
            dependency_outcomes=values["dependency_outcomes"],
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["classification_workload"]["excluded"].pop(),
        lambda payload: payload["classification_result_index"].update({"result_sha256": "0" * 64}),
        lambda payload: payload["source_note_inventory"].update({"inventory_sha256": "0" * 64}),
        lambda payload: payload["replacement_candidate"].update({"confidence": 0.01}),
        lambda payload: payload["proposal"].update({"review_subjects": []}),
    ],
    ids=(
        "excluded-ledger",
        "result-index",
        "source-inventory",
        "replacement-candidate",
        "proposal",
    ),
)
def test_manifest_reopen_rejects_tampered_typed_evidence(
    temporal_evidence: TemporalAnalysisEvidence,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    with pytest.raises(ValueError):
        TemporalAnalysisEvidence.from_canonical_bytes(_tampered_bytes(temporal_evidence, mutate))


def test_manifest_reopen_rejects_noncanonical_and_oversized_bytes(
    temporal_evidence: TemporalAnalysisEvidence,
) -> None:
    with pytest.raises(ValueError, match="not exact canonical JSON"):
        TemporalAnalysisEvidence.from_canonical_bytes(temporal_evidence.canonical_bytes() + b"\n")
    with pytest.raises(ValueError, match="16 MiB"):
        TemporalAnalysisEvidence.from_canonical_bytes(
            b" " * (MAX_TEMPORAL_ANALYSIS_MANIFEST_CANONICAL_BYTES_V1 + 1)
        )
