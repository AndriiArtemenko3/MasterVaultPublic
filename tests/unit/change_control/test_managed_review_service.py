"""Authoritative facade tests for V2 managed revision review."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from managed_v2_test_support import (
    RealManagedV2Scenario,
    build_real_managed_v2_scenario,
    clone_real_managed_v2_scenario,
)

from mastervault.change_control.incoming import ALIGNMENT_ATTESTATION_RELATIVE_PATH
from mastervault.change_control.managed_review import (
    AuthorityRevisionBinding,
    ManagedGenerationManifestBindingV2,
    ManagedRevisionDisposition,
    ManagedRevisionPlan,
    ManagedRevisionPlanningAdmissionBinding,
    ManagedRunBindingV2,
    NoChangeImpactCard,
)
from mastervault.change_control.managed_review_service import (
    ManagedReviewSelectionError,
    ManagedRevisionReviewSelection,
    decide_managed_revision_review,
    open_managed_revision_review,
)
from mastervault.change_control.managed_store import (
    ManagedReviewAuthorityError,
    ManagedRevisionEditDeferredError,
    ManagedRevisionStoreLifecycle,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.store import ChangeControlIdempotencyError


@pytest.fixture(scope="module")
def managed_service_seed(tmp_path_factory: pytest.TempPathFactory) -> RealManagedV2Scenario:
    scenario = build_real_managed_v2_scenario(
        tmp_path_factory.mktemp("managed-service-seed")
    )
    scenario.store.close()
    return scenario


def _open(scenario, *, operation_id: str = "managed-service:open"):
    return open_managed_revision_review(
        store=scenario.store,
        run_binding=scenario.run_binding,
        admitted_subjects=scenario.subjects,
        reviewed_snapshot=scenario.reviewed_snapshot,
        resolver=scenario.resolver,
        verified_bootstrap=scenario.verified_bootstrap,
        prechange_head=scenario.prechange_head,
        operation_id=operation_id,
        requester_id="operator@example.test",
        rationale="Review the complete admitted managed revision set.",
    )


def _repository_state(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_real_recorded_v2_open_is_authoritative_and_replayable(tmp_path: Path) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        opened = open_managed_revision_review(
            store=scenario.store,
            run_binding=scenario.run_binding,
            admitted_subjects=scenario.subjects,
            reviewed_snapshot=scenario.reviewed_snapshot,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:open",
            requester_id="operator@example.test",
            rationale="Review the complete admitted managed revision set.",
        )
        replay = open_managed_revision_review(
            store=scenario.store,
            run_binding=scenario.run_binding,
            admitted_subjects=scenario.subjects,
            reviewed_snapshot=scenario.reviewed_snapshot,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:open",
            requester_id="operator@example.test",
            rationale="Review the complete admitted managed revision set.",
        )

        assert opened == replay
        assert opened.lifecycle == ManagedRevisionStoreLifecycle.OPEN
        assert opened.request_record.command.bundle.run_binding == scenario.run_binding
        assert tuple(
            item.subject for item in opened.request_record.command.bundle.targets
        ) == scenario.subjects
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_request_records"
            ).fetchone()[0]
            == 1
        )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
            ).fetchone()[0]
            == 2
        )
    finally:
        scenario.store.close()


def test_service_decision_derives_mixed_outcomes_and_keeps_effects_inactive(
    tmp_path: Path,
) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        canonical_before = _repository_state(scenario.canonical_root)
        authority_before = scenario.store.get_active_generation(
            scenario.run_binding.prechange_head.aggregate_id,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        opened = open_managed_revision_review(
            store=scenario.store,
            run_binding=scenario.run_binding,
            admitted_subjects=scenario.subjects,
            reviewed_snapshot=scenario.reviewed_snapshot,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:mixed-open",
            requester_id="operator@example.test",
            rationale="Review one affected plan and one no-change result.",
        )
        selections = tuple(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=(
                    ManagedRevisionDisposition.APPROVE
                    if isinstance(target.subject, ManagedRevisionPlan)
                    else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                ),
            )
            for target in opened.request_record.command.bundle.targets
        )
        decided = decide_managed_revision_review(
            store=scenario.store,
            request_id=opened.request_record.command.request_id,
            selections=selections,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:mixed-decision",
            reviewer_id="reviewer@example.test",
            rationale="Approve the plan and confirm the grounded no-change result.",
        )
        replay = decide_managed_revision_review(
            store=scenario.store,
            request_id=opened.request_record.command.request_id,
            selections=selections,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:mixed-decision",
            reviewer_id="reviewer@example.test",
            rationale="Approve the plan and confirm the grounded no-change result.",
        )

        assert decided == replay
        assert decided.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
        assert decided.decision_record is not None
        manifest = decided.decision_record.command.generation_manifest
        assert isinstance(manifest, ManagedGenerationManifestBindingV2)
        assert (
            manifest.governing_source_adoption
            == scenario.run_binding.governing_source_adoption
        )
        approved_plan_keys = {
            item.target_key
            for item in opened.request_record.command.bundle.targets
            if isinstance(item.subject, ManagedRevisionPlan)
        }
        assert {item.target_key for item in manifest.publication_delta} == approved_plan_keys
        governing_paths = {
            scenario.run_binding.governing_source_adoption.raw_artifact.path,
            scenario.run_binding.governing_source_adoption.source_note_artifact.path,
        }
        assert governing_paths.isdisjoint(
            item.destination.path for item in manifest.publication_delta
        )
        assert decided.receipt is not None and decided.receipt.activation_required
        assert decided.receipt.generation_activated is False
        assert scenario.store.get_active_generation(
            scenario.run_binding.prechange_head.aggregate_id,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        ) == authority_before
        assert _repository_state(scenario.canonical_root) == canonical_before
        assert (
                scenario.store.conn.execute(
                    "SELECT count(*) FROM change_control_generation_manifests "
                    "WHERE manifest_kind='managed-overlay' AND created_inactive=1"
                ).fetchone()[0]
            == 1
        )
    finally:
        scenario.store.close()


def test_empty_subjects_fail_before_any_managed_review_write(
    tmp_path: Path,
) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            open_managed_revision_review(
                store=scenario.store,
                run_binding=scenario.run_binding,
                admitted_subjects=(),
                reviewed_snapshot=scenario.reviewed_snapshot,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                operation_id="managed-service:empty",
                requester_id="operator@example.test",
                rationale="This must not create a review.",
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_request_records"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


def test_selection_set_is_store_derived_complete_and_edit_is_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        opened = _open(scenario, operation_id="managed-service:selection-open")
        targets = opened.request_record.command.bundle.targets
        plan_target = next(
            item for item in targets if isinstance(item.subject, ManagedRevisionPlan)
        )
        no_change_target = next(
            item for item in targets if isinstance(item.subject, NoChangeImpactCard)
        )
        valid = (
            ManagedRevisionReviewSelection(
                target_id=plan_target.target_id,
                disposition=ManagedRevisionDisposition.APPROVE,
            ),
            ManagedRevisionReviewSelection(
                target_id=no_change_target.target_id,
                disposition=ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
            ),
        )
        invalid_sets = (
            valid[:1],
            (valid[0], valid[0]),
            (
                valid[0],
                ManagedRevisionReviewSelection(
                    target_id="mtarget:" + "0" * 64,
                    disposition=ManagedRevisionDisposition.REJECT,
                ),
            ),
            (
                ManagedRevisionReviewSelection(
                    target_id=plan_target.target_id,
                    disposition=ManagedRevisionDisposition.CONFIRM_NO_CHANGE,
                ),
                valid[1],
            ),
            (
                valid[0],
                ManagedRevisionReviewSelection(
                    target_id=no_change_target.target_id,
                    disposition=ManagedRevisionDisposition.APPROVE,
                ),
            ),
        )
        for index, selections in enumerate(invalid_sets):
            with pytest.raises(ManagedReviewSelectionError):
                decide_managed_revision_review(
                    store=scenario.store,
                    request_id=opened.request_record.command.request_id,
                    selections=selections,
                    resolver=scenario.resolver,
                    verified_bootstrap=scenario.verified_bootstrap,
                    prechange_head=scenario.prechange_head,
                    operation_id=f"managed-service:invalid-{index}",
                    reviewer_id="reviewer@example.test",
                    rationale="Reject an invalid human selection set.",
                )

        def unexpected_read(*args, **kwargs):
            del args, kwargs
            raise AssertionError("EDIT must fail before authoritative read")

        monkeypatch.setattr(scenario.store, "get_managed_review", unexpected_read)
        with pytest.raises(ManagedRevisionEditDeferredError):
            decide_managed_revision_review(
                store=scenario.store,
                request_id=opened.request_record.command.request_id,
                selections=(
                    ManagedRevisionReviewSelection(
                        target_id=plan_target.target_id,
                        disposition=ManagedRevisionDisposition.EDIT,
                    ),
                    valid[1],
                ),
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                operation_id="managed-service:edit-deferred",
                reviewer_id="reviewer@example.test",
                rationale="EDIT is not admitted in PR-A.",
            )
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_decisions"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


def test_all_reject_derives_noop_generation_without_activation(tmp_path: Path) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        opened = _open(scenario, operation_id="managed-service:reject-open")
        authority_before = opened.current_authority
        selections = tuple(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=ManagedRevisionDisposition.REJECT,
            )
            for target in opened.request_record.command.bundle.targets
        )
        decided = decide_managed_revision_review(
            store=scenario.store,
            request_id=opened.request_record.command.request_id,
            selections=selections,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:reject-decision",
            reviewer_id="reviewer@example.test",
            rationale="Reject every proposed managed outcome atomically.",
        )

        assert decided.receipt is not None
        assert not decided.receipt.activation_required
        assert decided.receipt.authorized_generation == authority_before.active_generation
        assert decided.current_authority == authority_before
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_generation_manifests "
                "WHERE manifest_kind='managed-overlay'"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


def _run_with_repository_id(run: ManagedRunBindingV2, repository_id: str) -> ManagedRunBindingV2:
    admission = run.revision_planning_admission
    completion_payload = {
        "schema_version": 1,
        "run_id": admission.run_id,
        "repository_id": repository_id,
        "manifest_id": admission.staging_manifest_id,
        "manifest_sha256": admission.staging_manifest_sha256,
        "manifest_path": admission.staging_manifest_path,
        "completion_path": admission.staging_completion_path,
    }
    completion_sha = hashlib.sha256(canonical_json_bytes(completion_payload)).hexdigest()
    rebound = ManagedRevisionPlanningAdmissionBinding.create(
        run_id=admission.run_id,
        repository_id=repository_id,
        workload_id=admission.workload_id,
        workload_sha256=admission.workload_sha256,
        analysis_set=admission.analysis_set,
        analysis_set_id=admission.analysis_set_id,
        analysis_set_sha256=admission.analysis_set_sha256,
        reviewed_snapshot_binding_id=admission.reviewed_snapshot_binding_id,
        reviewed_snapshot_binding_sha256=admission.reviewed_snapshot_binding_sha256,
        temporal_decision_record_sha256=admission.temporal_decision_record_sha256,
        contract_binding_id=admission.contract_binding_id,
        batch_id=admission.batch_id,
        batch_sha256=admission.batch_sha256,
        batch_members=admission.batch_members,
        staging_manifest_id=admission.staging_manifest_id,
        staging_manifest_sha256=admission.staging_manifest_sha256,
        staging_manifest_path=admission.staging_manifest_path,
        staging_completion_id=f"managed-staging-completion:{completion_sha}",
        staging_completion_sha256=completion_sha,
        staging_completion_path=admission.staging_completion_path,
        targets=admission.targets,
    )
    return ManagedRunBindingV2.create(
        run_id=run.run_id,
        operation_id=run.operation_id,
        prechange_head=run.prechange_head,
        analysis_head=run.analysis_head,
        algorithm_manifest_sha256=run.algorithm_manifest_sha256,
        inference_contract=run.inference_contract,
        analysis_set=run.analysis_set,
        revision_planning_admission=rebound,
        governing_source_adoption=run.governing_source_adoption,
    )


def test_wrong_reviewed_repository_lineage_fails_before_store_access(
    tmp_path: Path,
) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    try:
        with pytest.raises(ValueError, match="governing-source adoption"):
            _run_with_repository_id(scenario.run_binding, "f" * 64)
        assert (
            scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_request_records"
            ).fetchone()[0]
            == 0
        )
    finally:
        scenario.store.close()


def test_request_lost_acknowledgement_converges_from_authoritative_reread(
    managed_service_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(
        managed_service_seed, tmp_path / "lost-request-ack"
    )
    try:
        original = scenario.store.create_managed_review_request

        def commit_then_lose_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected request acknowledgement loss")

        monkeypatch.setattr(
            scenario.store, "create_managed_review_request", commit_then_lose_ack
        )
        opened = _open(scenario, operation_id="managed-service:lost-request-ack")
        assert opened.lifecycle == ManagedRevisionStoreLifecycle.OPEN
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_records"
        ).fetchone()[0] == 1
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
        ).fetchone()[0] == 1
    finally:
        scenario.store.close()


def test_request_operation_finder_and_replay_ignore_later_live_pointer(
    managed_service_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(
        managed_service_seed, tmp_path / "request-operation-replay"
    )
    operation_id = "managed-service:pointer-stable-open"
    try:
        assert scenario.store.find_managed_review_request_by_operation_id(
            "managed-service:unknown-request",
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        ) is None
        with pytest.raises(ChangeControlIdempotencyError):
            scenario.store.find_managed_review_request_by_operation_id(
                scenario.run_binding.operation_id,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
            )

        opened = _open(scenario, operation_id=operation_id)
        exact_record = scenario.store.find_managed_review_request_by_operation_id(
            operation_id,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
        )
        assert exact_record == opened.request_record
        selections = tuple(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=(
                    ManagedRevisionDisposition.APPROVE
                    if isinstance(target.subject, ManagedRevisionPlan)
                    else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                ),
            )
            for target in opened.request_record.command.bundle.targets
        )
        decided = decide_managed_revision_review(
            store=scenario.store,
            request_id=opened.request_record.command.request_id,
            selections=selections,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:pointer-stable-decision",
            reviewer_id="reviewer@example.test",
            rationale="Authorize an inactive successor for pointer-race simulation.",
        )
        assert decided.decision_record is not None
        successor = AuthorityRevisionBinding.create_managed_successor(
            expected_authority=opened.current_authority,
            decision_record=decided.decision_record,
        )
        original_get = scenario.store.get_managed_review

        def advanced_view(*args, **kwargs):
            return replace(original_get(*args, **kwargs), current_authority=successor)

        def forbidden_live_authority(*args, **kwargs):
            del args, kwargs
            raise AssertionError("exact request replay must not consult the live pointer")

        monkeypatch.setattr(scenario.store, "get_managed_review", advanced_view)
        monkeypatch.setattr(
            scenario.store,
            "get_active_generation",
            forbidden_live_authority,
        )
        replay = _open(scenario, operation_id=operation_id)
        assert replay.current_authority == successor
        assert replay.request_record == exact_record
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
        ).fetchone()[0] == 2

        with pytest.raises(ChangeControlIdempotencyError):
            open_managed_revision_review(
                store=scenario.store,
                run_binding=scenario.run_binding,
                admitted_subjects=scenario.subjects,
                reviewed_snapshot=scenario.reviewed_snapshot,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                operation_id=operation_id,
                requester_id="different-operator@example.test",
                rationale="A different immutable opening must not reuse the operation.",
            )
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
        ).fetchone()[0] == 2
    finally:
        scenario.store.close()


def test_governing_source_mutation_blocks_create_read_decide_and_replay(
    tmp_path: Path,
) -> None:
    scenario = build_real_managed_v2_scenario(tmp_path)
    adoption = scenario.run_binding.governing_source_adoption
    raw_path = scenario.canonical_root / adoption.raw_artifact.path
    note_path = scenario.canonical_root / adoption.source_note_artifact.path
    manifest_path = scenario.canonical_root / adoption.incoming_manifest_path
    alignment_path = scenario.canonical_root / ALIGNMENT_ATTESTATION_RELATIVE_PATH
    raw_bytes = raw_path.read_bytes()
    note_bytes = note_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    alignment_bytes = alignment_path.read_bytes()
    try:
        for index, (path, content) in enumerate(
            (
                (raw_path, raw_bytes),
                (manifest_path, manifest_bytes),
                (alignment_path, alignment_bytes),
            )
        ):
            path.write_bytes(content + b"\nforged before open\n")
            with pytest.raises(ManagedReviewAuthorityError):
                _open(
                    scenario,
                    operation_id=f"managed-service:mutated-source-open-{index}",
                )
            assert scenario.store.conn.execute(
                "SELECT count(*) FROM change_control_managed_review_request_records"
            ).fetchone()[0] == 0
            path.write_bytes(content)

        opened = _open(scenario, operation_id="managed-service:source-guard-open")
        note_path.write_bytes(note_bytes + b"\nforged after open\n")
        with pytest.raises(ManagedReviewAuthorityError):
            scenario.store.get_managed_review(
                opened.request_record.command.request_id,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
            )
        with pytest.raises(ManagedReviewAuthorityError):
            _open(scenario, operation_id="managed-service:source-guard-open")
        selections = tuple(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=(
                    ManagedRevisionDisposition.APPROVE
                    if isinstance(target.subject, ManagedRevisionPlan)
                    else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                ),
            )
            for target in opened.request_record.command.bundle.targets
        )
        with pytest.raises(ManagedReviewAuthorityError):
            decide_managed_revision_review(
                store=scenario.store,
                request_id=opened.request_record.command.request_id,
                selections=selections,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
                operation_id="managed-service:mutated-source-decision",
                reviewer_id="reviewer@example.test",
                rationale="Mutated governing evidence must block every authority boundary.",
            )
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0] == 0
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_request_delivery_receipts"
        ).fetchone()[0] == 1
    finally:
        raw_path.write_bytes(raw_bytes)
        note_path.write_bytes(note_bytes)
        manifest_path.write_bytes(manifest_bytes)
        alignment_path.write_bytes(alignment_bytes)
        scenario.store.close()


def test_decision_lost_acknowledgement_converges_without_duplicate_authority(
    managed_service_seed: RealManagedV2Scenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = clone_real_managed_v2_scenario(
        managed_service_seed, tmp_path / "lost-decision-ack"
    )
    try:
        opened = _open(scenario, operation_id="managed-service:lost-decision-open")
        selections = tuple(
            ManagedRevisionReviewSelection(
                target_id=target.target_id,
                disposition=(
                    ManagedRevisionDisposition.APPROVE
                    if isinstance(target.subject, ManagedRevisionPlan)
                    else ManagedRevisionDisposition.CONFIRM_NO_CHANGE
                ),
            )
            for target in opened.request_record.command.bundle.targets
        )
        original = scenario.store.decide_managed_review
        original_get = scenario.store.get_managed_review
        advanced_authority: list[AuthorityRevisionBinding] = []

        def commit_then_lose_ack(*args, **kwargs):
            original(*args, **kwargs)
            committed = original_get(
                opened.request_record.command.request_id,
                resolver=scenario.resolver,
                verified_bootstrap=scenario.verified_bootstrap,
                prechange_head=scenario.prechange_head,
            )
            assert committed.decision_record is not None
            advanced_authority.append(
                AuthorityRevisionBinding.create_managed_successor(
                    expected_authority=committed.current_authority,
                    decision_record=committed.decision_record,
                )
            )
            raise RuntimeError("injected decision acknowledgement loss")

        def advanced_view(*args, **kwargs):
            view = original_get(*args, **kwargs)
            return (
                replace(view, current_authority=advanced_authority[0])
                if advanced_authority
                else view
            )

        monkeypatch.setattr(scenario.store, "decide_managed_review", commit_then_lose_ack)
        monkeypatch.setattr(scenario.store, "get_managed_review", advanced_view)
        decided = decide_managed_revision_review(
            store=scenario.store,
            request_id=opened.request_record.command.request_id,
            selections=selections,
            resolver=scenario.resolver,
            verified_bootstrap=scenario.verified_bootstrap,
            prechange_head=scenario.prechange_head,
            operation_id="managed-service:lost-decision-ack",
            reviewer_id="reviewer@example.test",
            rationale="Approve the exact bundle despite a lost response acknowledgement.",
        )
        assert decided.lifecycle == ManagedRevisionStoreLifecycle.DECIDED
        assert decided.current_authority == advanced_authority[0]
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decisions"
        ).fetchone()[0] == 1
        assert scenario.store.conn.execute(
            "SELECT count(*) FROM change_control_managed_review_decision_delivery_receipts"
        ).fetchone()[0] == 1
    finally:
        scenario.store.close()
