from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mastervault.change_control.application_no_work import (
    NoWorkPlanningEvidenceError,
    NoWorkPlanningEvidenceRepository,
    NoWorkPlanningEvidenceV1,
)
from mastervault.change_control.managed_revision_planning import RevisionPlanningWorkload
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.revision_planning_inference import (
    derive_revision_planning_eligibility_from_impact_evidence,
)

RUN_ID = f"operatorrun:{'a' * 64}"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _receipt(
    *,
    recorded_at: str = "2026-08-20T12:00:00+00:00",
    run_id: str = RUN_ID,
    reviewed_sha256: str = "c" * 64,
) -> NoWorkPlanningEvidenceV1:
    workload_sha = "b" * 64
    result_payload = {
        "namespace": "mastervault.actual-impact-result-index.v1",
        "schema_version": 1,
        "workload_id": f"impactwork:{workload_sha}",
        "workload_sha256": workload_sha,
        "decision_count": 0,
        "output_shards": [],
    }
    result_sha = hashlib.sha256(canonical_json_bytes(result_payload)).hexdigest()
    eligibility = derive_revision_planning_eligibility_from_impact_evidence(
        workload_id=f"impactwork:{workload_sha}",
        workload_sha256=workload_sha,
        result_id=f"impactresult:{result_sha}",
        result_sha256=result_sha,
        input_shards=(),
        output_shards=(),
    )
    return NoWorkPlanningEvidenceV1.create(
        run_id=run_id,
        reviewed_snapshot_binding_id=f"reviewed-snapshot:{reviewed_sha256}",
        reviewed_snapshot_binding_sha256=reviewed_sha256,
        impact_evidence_binding_id=f"mimpactevidence:{'d' * 64}",
        impact_evidence_binding_sha256="d" * 64,
        configuration_sha256="e" * 64,
        impact_input_shards=(),
        impact_output_shards=(),
        workload=RevisionPlanningWorkload.create(
            eligibility=eligibility,
            input_shards=(),
        ),
        recorded_at=recorded_at,
    )


def test_no_work_receipt_reproduces_and_exact_retry_retains_timestamp(tmp_path: Path) -> None:
    root = _root(tmp_path)
    repository = NoWorkPlanningEvidenceRepository(root)
    first = repository.persist(_receipt())
    retried = repository.persist(_receipt(recorded_at="2026-08-20T12:00:01+00:00"))

    assert retried == first
    assert retried.recorded_at == "2026-08-20T12:00:00+00:00"
    assert repository.reopen(
        first.run_id, first.workload.workload_id, first.workload.workload_sha256
    ) == first


def test_no_work_read_only_reopen_changes_no_files_or_mtimes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    NoWorkPlanningEvidenceRepository(root).persist(_receipt())
    before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    reader = NoWorkPlanningEvidenceRepository(root, create=False, read_only=True)
    expected = _receipt()
    assert reader.reopen(
        expected.run_id, expected.workload.workload_id, expected.workload.workload_sha256
    ) == expected
    after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_no_work_tamper_fails_reproduction_or_identity(tmp_path: Path) -> None:
    root = _root(tmp_path)
    repository = NoWorkPlanningEvidenceRepository(root)
    repository.persist(_receipt())
    receipt = _receipt()
    locator = NoWorkPlanningEvidenceRepository.relative_locator(
        receipt.run_id,
        receipt.workload.workload_id,
        receipt.workload.workload_sha256,
    )
    path = root / locator
    path.write_bytes(path.read_bytes().replace(b'"configuration_sha256":"e', b'"configuration_sha256":"f'))

    with pytest.raises(NoWorkPlanningEvidenceError, match="invalid"):
        NoWorkPlanningEvidenceRepository(root, create=False, read_only=True).reopen(
            receipt.run_id,
            receipt.workload.workload_id,
            receipt.workload.workload_sha256,
        )


def test_same_workload_is_independent_per_run_and_rejects_wrong_run_reopen(
    tmp_path: Path,
) -> None:
    repository = NoWorkPlanningEvidenceRepository(_root(tmp_path))
    first = repository.persist(_receipt())
    second = repository.persist(_receipt(run_id=f"operatorrun:{'f' * 64}"))

    assert first.workload == second.workload
    assert first.evidence_id != second.evidence_id
    assert repository.reopen(
        second.run_id, second.workload.workload_id, second.workload.workload_sha256
    ) == second
    with pytest.raises(NoWorkPlanningEvidenceError, match="does not exist"):
        repository.reopen(
            f"operatorrun:{'9' * 64}",
            first.workload.workload_id,
            first.workload.workload_sha256,
        )


def test_same_run_workload_rejects_different_upstream_receipt(tmp_path: Path) -> None:
    repository = NoWorkPlanningEvidenceRepository(_root(tmp_path))
    repository.persist(_receipt())

    with pytest.raises(NoWorkPlanningEvidenceError, match="different immutable inputs"):
        repository.persist(_receipt(reviewed_sha256="9" * 64))
