from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError
from test_managed_review import (
    SHA_C,
    SHA_D,
    SHA_F,
    Context,
    _bundle,
    _context,
    _negative,
    _plan,
    _predecessor,
)

from mastervault.change_control.managed_review import (
    ManagedAnalysisSetBinding,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedImpactBatchMemberBinding,
    ManagedImpactOutputRefBinding,
    ManagedRevisionReviewBundle,
    ManagedRunBinding,
)
from mastervault.change_control.managed_store import (
    ManagedReviewAuthorityError,
    SqliteManagedChangeControlStore,
)
from mastervault.change_control.models import canonical_json_bytes

LEGACY_ANALYSIS_CANONICAL_SHA256 = "c508e4b57106eff6eb55fda5c2fc2fd5d28b2b4aef9fdd6d1d15fcbd2859948e"
LEGACY_BUNDLE_CANONICAL_SHA256 = "ca070613587e6af2b6ca0e59dc3494c3e504053d667471c080477e0b8c8e469b"


def _fake_evidence(
    key: str,
    *,
    disposition: str = "NO_CHANGE_REQUIRED",
    output_sha256: str | None = None,
) -> ManagedImpactAnalysisEvidenceBinding:
    document, _raw, _note = _predecessor(key)
    target_sha = output_sha256 or hashlib.sha256(key.encode()).hexdigest()
    batch_sha = "7" * 64
    workload_sha = "8" * 64
    result_sha = "e" * 64
    return ManagedImpactAnalysisEvidenceBinding.create(
        repository_id="6" * 64,
        batch_id=f"inference-batch:{batch_sha}",
        batch_sha256=batch_sha,
        batch_members=(
            ManagedImpactBatchMemberBinding(
                execution_id="inference-exec:" + "9" * 64,
                receipt_artifact_id="martifact:" + "a" * 64,
                outcome_sha256="b" * 64,
            ),
        ),
        workload_id=f"impactwork:{workload_sha}",
        workload_sha256=workload_sha,
        result_id=f"impactresult:{result_sha}",
        result_sha256=result_sha,
        output_shards=(
            ManagedImpactOutputRefBinding(
                document_version_id=document.document_version_id,
                input_shard_id="impactin:" + "c" * 64,
                input_shard_sha256="c" * 64,
                output_shard_id=f"impactout:{target_sha}",
                output_shard_sha256=target_sha,
                decision_count=1,
                document_disposition=disposition,
            ),
        ),
    )


def _v2_context(
    key: str,
    *,
    disposition: str = "NO_CHANGE_REQUIRED",
    output_sha256: str | None = None,
) -> Context:
    legacy = _context()
    evidence = _fake_evidence(
        key,
        disposition=disposition,
        output_sha256=output_sha256,
    )
    analysis = ManagedAnalysisSetBinding.create_with_impact_evidence(
        analysis_bootstrap=legacy.analysis_set.analysis_bootstrap,
        candidate_result_sha256=SHA_C,
        classification_result_sha256=SHA_D,
        attention_result_sha256=SHA_F,
        impact_evidence=evidence,
        global_relevant_claim_revision_ids=(
            legacy.analysis_set.global_relevant_claim_revision_ids
        ),
    )
    run = ManagedRunBinding.create(
        run_id=legacy.run.run_id,
        operation_id=legacy.run.operation_id,
        prechange_head=legacy.run.prechange_head,
        analysis_head=legacy.run.analysis_head,
        algorithm_manifest_sha256=legacy.run.algorithm_manifest_sha256,
        inference_contract=legacy.run.inference_contract,
        analysis_set=analysis,
    )
    return Context(
        analysis_set=analysis,
        run=run,
        review_base=legacy.review_base,
        prerequisite=legacy.prerequisite,
    )


def test_v1_analysis_and_complete_bundle_remain_byte_identical_and_readable() -> None:
    context = _context()
    analysis_bytes = canonical_json_bytes(context.analysis_set.model_dump(mode="json"))
    bundle = _bundle(context, _negative("compat", context))
    bundle_bytes = canonical_json_bytes(bundle.model_dump(mode="json"))

    assert "impact_evidence" not in context.analysis_set.model_dump(mode="json")
    assert hashlib.sha256(analysis_bytes).hexdigest() == LEGACY_ANALYSIS_CANONICAL_SHA256
    assert hashlib.sha256(bundle_bytes).hexdigest() == LEGACY_BUNDLE_CANONICAL_SHA256
    assert ManagedAnalysisSetBinding.model_validate_json(analysis_bytes) == context.analysis_set
    assert ManagedRevisionReviewBundle.model_validate_json(bundle_bytes) == bundle
    with pytest.raises(ValueError, match="requires v2 durable impact evidence"):
        bundle.require_authoritative_impact_evidence()


def test_v1_bundle_is_readable_but_cannot_cross_new_store_authority_boundary() -> None:
    context = _context()
    bundle = _bundle(context, _negative("compat", context))

    with pytest.raises(ManagedReviewAuthorityError) as exc_info:
        SqliteManagedChangeControlStore._resolve_contract_and_artifacts(
            bundle,
            object(),  # type: ignore[arg-type]
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "requires v2 impact evidence" in str(exc_info.value.__cause__)


def test_v2_factory_requires_exact_evidence_and_changes_identity() -> None:
    context = _v2_context("compat")
    evidence = context.analysis_set.impact_evidence
    assert evidence is not None
    assert context.analysis_set.schema_version == 2
    assert context.analysis_set.impact_result_sha256 == evidence.result_sha256
    assert context.analysis_set.analysis_set_id != _context().analysis_set.analysis_set_id

    missing = context.analysis_set.model_dump(mode="json")
    missing.pop("impact_evidence")
    with pytest.raises(ValidationError, match="requires durable impact evidence"):
        ManagedAnalysisSetBinding.model_validate_json(canonical_json_bytes(missing))

    legacy_with_evidence = _context().analysis_set.model_dump(mode="json")
    legacy_with_evidence["impact_evidence"] = evidence.model_dump(mode="json")
    with pytest.raises(ValidationError, match="v1 cannot carry"):
        ManagedAnalysisSetBinding.model_validate_json(canonical_json_bytes(legacy_with_evidence))


def test_evidence_rejects_batch_substitution_and_missing_or_duplicate_members() -> None:
    evidence = _fake_evidence("compat")
    substituted = evidence.model_dump(mode="json")
    substituted["batch_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="batch ID differs"):
        ManagedImpactAnalysisEvidenceBinding.model_validate_json(
            canonical_json_bytes(substituted)
        )

    missing = evidence.model_dump(mode="json")
    missing["batch_members"] = []
    with pytest.raises(ValidationError):
        ManagedImpactAnalysisEvidenceBinding.model_validate_json(canonical_json_bytes(missing))

    duplicate = evidence.model_dump(mode="json")
    duplicate["batch_members"] = duplicate["batch_members"] * 2
    duplicate["output_shards"] = duplicate["output_shards"] * 2
    with pytest.raises(ValidationError, match="execution IDs must be unique"):
        ManagedImpactAnalysisEvidenceBinding.model_validate_json(
            canonical_json_bytes(duplicate)
        )


def test_v2_bundle_requires_exact_output_coverage_and_result_sha() -> None:
    context = _v2_context("compat")
    card = _negative("compat", context)
    bundle = _bundle(context, card)

    assert bundle.require_authoritative_impact_evidence() == context.analysis_set.impact_evidence

    with pytest.raises(ValidationError, match="exactly cover"):
        _bundle(context, _negative("another-target", context))

    mismatched = _v2_context("compat", output_sha256="0" * 64)
    with pytest.raises(ValidationError, match="exact impact output shard SHA"):
        _bundle(mismatched, _negative("compat", mismatched))


def test_v2_bundle_enforces_locally_selected_disposition_and_blocks_unresolved() -> None:
    affected = _v2_context("compat", disposition="AFFECTED")
    with pytest.raises(ValidationError, match="requires a managed revision plan"):
        _bundle(affected, _negative("compat", affected))
    assert _bundle(affected, _plan("compat", affected)).targets[0].subject.kind == (
        "proposed-revision"
    )

    no_change = _v2_context("compat", disposition="NO_CHANGE_REQUIRED")
    with pytest.raises(ValidationError, match="requires an explicit no-change card"):
        _bundle(no_change, _plan("compat", no_change))

    unresolved = _v2_context("compat", disposition="UNRESOLVED")
    with pytest.raises(ValidationError, match="unresolved impact output"):
        _bundle(unresolved, _negative("compat", unresolved))
