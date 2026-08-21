"""Production navigation resolver for synchronous application-owned runs.

The private lifecycle index only supplies locators.  Every method independently
reopens the owning SQLite row or immutable repository artifact and compares its
identity with both the index and the requested navigation target.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceOwnerV1,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.application_mechanical_no_change import (
    MechanicalNoChangeEvidenceRepository,
    MechanicalNoChangeEvidenceV1,
)
from mastervault.change_control.application_no_work import NoWorkPlanningEvidenceRepository
from mastervault.change_control.application_stage_evidence import (
    ApplicationStageEvidenceRepository,
)
from mastervault.change_control.classification import ClassificationResultSet
from mastervault.change_control.generic_analysis import GenericSourceNoteInventoryResolverV2
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    GenericEvidenceBundleReceiptV2,
)
from mastervault.change_control.impact_inference import RecordedImpactInferenceRun
from mastervault.change_control.impact_results import validate_impact_results
from mastervault.change_control.inference_repository import FilesystemInferenceEvidenceRepository
from mastervault.change_control.managed_impact_evidence import bind_recorded_impact_inference_run
from mastervault.change_control.managed_review import (
    ManagedArtifactRef,
    ManagedImpactAnalysisEvidenceBinding,
    ManagedNoWorkPlanningAdmissionBinding,
    ManagedRevisionReviewBundle,
    ManagedRunBindingV2,
)
from mastervault.change_control.managed_revision_admission import (
    ManagedRevisionPlanningAdmissionBinding,
    bind_no_work_planning_admission,
    reopen_revision_planning_admission,
    revision_planning_staging_completion,
)
from mastervault.change_control.managed_revision_planning import RevisionPlanningWorkload
from mastervault.change_control.managed_staging_repository import ManagedStagingRepository
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import RecordedInferenceTask
from mastervault.change_control.regression_baseline import (
    GenerationZeroBaselineReceiptV1,
    GenerationZeroBaselineRepository,
)
from mastervault.change_control.reviewed_snapshot import (
    RepositorySourceNoteInventoryResolver,
    ReviewedTemporalSnapshotAuthority,
    resolve_reviewed_temporal_snapshot,
)
from mastervault.change_control.store import SqliteChangeControlStore
from mastervault.change_control.synchronous_lifecycle_store_models import (
    GenerationZeroBaselineStoreRecordV1,
    IncomingAdmissionIntentV1,
)
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.temporal_proposal import TemporalProposalCommit


class ApplicationAuthorityResolutionError(ValueError):
    """A lifecycle locator failed to reopen its independent owner authority."""


type ApplicationSourceNoteResolver = (
    RepositorySourceNoteInventoryResolver | GenericSourceNoteInventoryResolverV2
)
type ApplicationSourceNoteResolverLoader = Callable[[str], ApplicationSourceNoteResolver]


class ApplicationOperatorRunAuthorityResolver:
    """Fresh read-only composite resolver used by status/list/verify/activation."""

    def __init__(
        self,
        *,
        evidence_root: Path,
        state_path: Path,
        source_note_resolver: (
            ApplicationSourceNoteResolver | ApplicationSourceNoteResolverLoader | None
        ) = None,
        configuration_sha256: str | None = None,
    ) -> None:
        self._state_path = Path(state_path)
        self._index = FilesystemLifecycleEvidenceIndex(
            Path(evidence_root), create=False, read_only=True
        )
        self._generic = FilesystemGenericIncomingRepositoryV2(
            Path(evidence_root), create=False, read_only=True
        )
        self._inference = FilesystemInferenceEvidenceRepository(
            Path(evidence_root), create=False, read_only=True
        )
        self._baseline = GenerationZeroBaselineRepository(
            Path(evidence_root), create=False, read_only=True
        )
        self._stages = ApplicationStageEvidenceRepository(
            Path(evidence_root), create=False, read_only=True
        )
        self._no_work = NoWorkPlanningEvidenceRepository(
            Path(evidence_root), create=False, read_only=True
        )
        self._mechanical_no_change = MechanicalNoChangeEvidenceRepository(
            Path(evidence_root), create=False, read_only=True
        )
        self._staging = ManagedStagingRepository(Path(evidence_root), create=False, read_only=True)
        self._source_note_resolver = source_note_resolver
        self._configuration_sha256 = configuration_sha256

    def _source_notes_for_run(self, run_id: str) -> ApplicationSourceNoteResolver:
        configured = self._source_note_resolver
        if configured is None:
            raise ApplicationAuthorityResolutionError(
                "reviewed temporal authority requires a configured SourceNote resolver"
            )
        resolver = configured(run_id) if callable(configured) else configured
        if type(resolver) not in {
            RepositorySourceNoteInventoryResolver,
            GenericSourceNoteInventoryResolverV2,
        }:
            raise ApplicationAuthorityResolutionError(
                "SourceNote resolver loader returned a substituted resolver"
            )
        return resolver

    def open_managed_artifact(self, *, run_id: str, artifact: ManagedArtifactRef) -> bytes:
        """Reopen one exact inference or completed-staging citation artifact."""

        if PurePosixPath(artifact.path).parts[:2] != ("staging", "managed-review"):
            return self._inference.open_artifact(artifact)
        planning = self._stages.reopen_planning(run_id)
        binding = planning.binding
        if binding.run_id != run_id:
            raise ApplicationAuthorityResolutionError(
                "planning stage evidence belongs to another operator run"
            )
        return self._staging.open_member(
            completion=revision_planning_staging_completion(binding),
            artifact=artifact,
        )

    @staticmethod
    def _one_owner(
        owners: tuple[LifecycleEvidenceOwnerV1, ...], *, kind: str
    ) -> LifecycleEvidenceOwnerV1:
        matches = tuple(item for item in owners if item.owner_kind == kind)
        if len(matches) != 1:
            raise ApplicationAuthorityResolutionError(
                f"lifecycle index requires exactly one {kind} owner"
            )
        return matches[0]

    def resolve_incoming_source(
        self, intent: IncomingAdmissionIntentV1
    ) -> GenericEvidenceBundleReceiptV2:
        index = self._index.reopen(intent.run_id, LifecycleEvidenceStageV1.INCOMING)
        owner = self._one_owner(index.owners, kind="generic-bundle")
        if not (
            owner.owner_id == intent.bundle_id
            and owner.owner_sha256 == intent.bundle_sha256
            and owner.relative_locator == f"generic-incoming/v2/bundles/{intent.bundle_sha256}.json"
        ):
            raise ApplicationAuthorityResolutionError(
                "incoming lifecycle locator differs from its SQLite intent"
            )
        capability = self._generic.reopen(intent.bundle_id)
        evidence = self._generic.resolve_verified_evidence(capability)
        bundle = evidence.bundle
        if not (
            bundle.bundle_id == intent.bundle_id
            and bundle.bundle_sha256 == intent.bundle_sha256
            and bundle.admission_sha256 == intent.admission_sha256
            and bundle.source_receipt_sha256 == intent.source_receipt_sha256
            and bundle.projection_sha256 == intent.projection_sha256
            and bundle.inference_sha256 == intent.inference_sha256
        ):
            raise ApplicationAuthorityResolutionError(
                "incoming repository authority differs from its SQLite intent"
            )
        return bundle

    def resolve_operator_mechanical_no_change(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> MechanicalNoChangeEvidenceV1:
        """Reopen and reproduce one classification-proven terminal no-op."""

        receipt = self._mechanical_no_change.reopen(
            run_id=run_id,
            evidence_id=target_id,
            evidence_sha256=target_sha256,
        )
        if self._configuration_sha256 is None or (
            receipt.configuration_sha256 != self._configuration_sha256
        ):
            raise ApplicationAuthorityResolutionError(
                "mechanical no-change configuration differs from the current runtime"
            )
        index = self._index.reopen(run_id, LifecycleEvidenceStageV1.CLASSIFICATION)
        owner = self._one_owner(index.owners, kind="classification-batch")
        if not (
            owner.owner_id == receipt.classification_batch_id
            and owner.owner_sha256 == receipt.classification_batch_sha256
            and owner.relative_locator
            == f"inference/evidence/batches/{receipt.classification_batch_sha256}.json"
        ):
            raise ApplicationAuthorityResolutionError(
                "mechanical no-change classification locator differs from its receipt"
            )
        outcomes, capability = self._inference.resolve_verified_batch(
            batch_id=receipt.classification_batch_id,
            batch_sha256=receipt.classification_batch_sha256,
        )
        workload = receipt.classification_results.workload
        expected_shards = tuple(
            (item.shard_id, item.shard_sha256) for item in workload.inference_shards
        )
        observed_shards = tuple(
            (
                item.execution.input_envelope.input_shard_id,
                item.execution.input_envelope.input_shard_sha256,
            )
            for item in outcomes
        )
        if not (
            capability.batch_id == receipt.classification_batch_id
            and capability.batch_sha256 == receipt.classification_batch_sha256
            and observed_shards == expected_shards
            and all(
                item.execution.task is RecordedInferenceTask.CLASSIFICATION
                and item.execution.contract == receipt.classification_contract
                and item.execution.input_envelope.workload_id == workload.workload_id
                and item.execution.input_envelope.workload_sha256 == workload.workload_sha256
                and item.classification_output is not None
                for item in outcomes
            )
        ):
            raise ApplicationAuthorityResolutionError(
                "mechanical no-change inference batch differs from its exact contract"
            )
        classifications = tuple(
            result.classification
            for item in outcomes
            for output in (item.classification_output,)
            if output is not None
            for result in output.items
        )
        reproduced = ClassificationResultSet.create(
            workload=workload,
            classifications=classifications,
        )
        if reproduced != receipt.classification_results:
            raise ApplicationAuthorityResolutionError(
                "mechanical no-change result does not reproduce from inference evidence"
            )

        store = SqliteManagedChangeControlStore(self._state_path, secure_open=True, read_only=True)
        try:
            links = {
                str(row["link_kind"]): str(row["target_id"])
                for row in store.conn.execute(
                    "SELECT link_kind,target_id FROM change_control_operator_run_links "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            }
            incoming_row = store.conn.execute(
                "SELECT intent_id FROM change_control_incoming_admission_receipts "
                "WHERE receipt_id=?",
                (links.get("incoming-source", ""),),
            ).fetchone()
            suite_row = store.conn.execute(
                "SELECT intent_id FROM change_control_regression_suite_admission_receipts "
                "WHERE receipt_id=?",
                (links.get("regression-suite", ""),),
            ).fetchone()
            incoming = (
                store._read_incoming_admission_in_transaction(str(incoming_row["intent_id"]))  # noqa: SLF001
                if incoming_row is not None
                else None
            )
            suite = (
                store._read_suite_admission_in_transaction(str(suite_row["intent_id"]))  # noqa: SLF001
                if suite_row is not None
                else None
            )
            baseline = store._read_baseline_in_transaction(  # noqa: SLF001
                links.get("generation-zero-baseline", "")
            )
        finally:
            store.close()
        aggregate_store = SqliteChangeControlStore(
            self._state_path, secure_open=True, read_only=True
        )
        try:
            snapshot = aggregate_store.load(receipt.generic_analysis.aggregate_id)
        finally:
            aggregate_store.close()
        if not (
            incoming == receipt.incoming_admission
            and suite == receipt.suite_admission
            and baseline is not None
            and baseline.baseline_receipt == receipt.baseline_receipt
            and snapshot is not None
            and snapshot.revision == receipt.generic_analysis.analysis_revision
            and snapshot.aggregate_sha256 == receipt.generic_analysis.analysis_aggregate_sha256
        ):
            raise ApplicationAuthorityResolutionError(
                "mechanical no-change receipt differs from freshly reopened upstream authority"
            )
        return receipt

    def resolve_temporal_proposal(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> TemporalProposalCommit:
        index = self._index.reopen(run_id, LifecycleEvidenceStageV1.TEMPORAL)
        owner = self._one_owner(index.owners, kind="temporal-analysis")
        manifest_sha = target_id.removeprefix("temporal-commit:")
        expected_locator = f"temporal/evidence/analyses/{manifest_sha}.json"
        if not (
            target_id == f"temporal-commit:{manifest_sha}"
            and owner.owner_id == f"temporal-analysis:{manifest_sha}"
            and owner.owner_sha256 == manifest_sha
            and owner.relative_locator == expected_locator
        ):
            raise ApplicationAuthorityResolutionError(
                "temporal lifecycle locator differs from its operation"
            )
        payload = self._inference.resolve_temporal_analysis_manifest(
            manifest_id=owner.owner_id,
            manifest_sha256=owner.owner_sha256,
        )
        evidence = TemporalAnalysisEvidence.from_canonical_bytes(payload)
        store = SqliteManagedChangeControlStore(self._state_path, secure_open=True, read_only=True)
        try:
            receipt = store.get_operation_commit(target_id)
            receipt_sha256 = store.get_operation_receipt_sha256(target_id)
        finally:
            store.close()
        if receipt is None or not (
            receipt_sha256 == target_sha256
            and receipt.aggregate_id == evidence.proposal.proposed_aggregate.aggregate_id
            and receipt.aggregate_sha256 == evidence.proposal.binding.proposed_aggregate_sha256
            and receipt.revision == 3
            and receipt.changed
        ):
            raise ApplicationAuthorityResolutionError(
                "temporal SQLite receipt differs from immutable analysis evidence"
            )
        return TemporalProposalCommit(
            proposal=evidence.proposal,
            operation_id=target_id,
            temporal_analysis_manifest_id=owner.owner_id,
            temporal_analysis_manifest_sha256=owner.owner_sha256,
            temporal_analysis_manifest_path=expected_locator,
            evidence_repository_id=self._inference.repository_id,
            aggregate_id=receipt.aggregate_id,
            revision=3,
            aggregate_sha256=receipt.aggregate_sha256,
            changed=True,
            committed_at=receipt.committed_at,
            replayed=True,
        )

    def _optional_managed_bundle(self, run_id: str) -> ManagedRevisionReviewBundle | None:
        store = SqliteManagedChangeControlStore(self._state_path, secure_open=True, read_only=True)
        try:
            rows = store.conn.execute(
                "SELECT bundle_id,bundle_sha256,payload_json "
                "FROM change_control_managed_review_bundles "
                "ORDER BY bundle_id"
            ).fetchall()
        finally:
            store.close()
        matches: list[ManagedRevisionReviewBundle] = []
        for row in rows:
            payload = str(row["payload_json"]).encode("utf-8")
            try:
                bundle = ManagedRevisionReviewBundle.model_validate_json(payload, strict=True)
            except ValueError as exc:
                raise ApplicationAuthorityResolutionError(
                    "managed-review SQLite bundle is invalid"
                ) from exc
            if not (
                canonical_json_bytes(bundle.model_dump(mode="json")) == payload
                and str(row["bundle_id"]) == bundle.bundle_id
                and str(row["bundle_sha256"]) == bundle.bundle_sha256
            ):
                raise ApplicationAuthorityResolutionError(
                    "managed-review SQLite bundle is not exact canonical authority"
                )
            if bundle.run_binding.run_id == run_id:
                matches.append(bundle)
        if not matches:
            return None
        if len(matches) != 1:
            raise ApplicationAuthorityResolutionError(
                "operator run owns duplicate managed review bundles"
            )
        return matches[0]

    def resolve_operator_impact_evidence(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> ManagedImpactAnalysisEvidenceBinding:
        index = self._index.reopen(run_id, LifecycleEvidenceStageV1.IMPACT)
        owner = self._one_owner(index.owners, kind="impact-stage-evidence")
        receipt = self._stages.reopen_impact(run_id)
        expected_locator = self._stages.relative_locator(run_id, "impact")
        reviewed = self._reviewed_snapshot(run_id)
        if not (
            owner.owner_id == receipt.evidence_id
            and owner.owner_sha256 == receipt.evidence_sha256
            and owner.relative_locator == expected_locator
            and receipt.binding.evidence_binding_id == target_id
            and receipt.binding.evidence_binding_sha256 == target_sha256
            and receipt.reviewed_snapshot_binding_id == reviewed.binding.binding_id
            and receipt.reviewed_snapshot_binding_sha256 == reviewed.binding.binding_sha256
            and self._configuration_sha256 is not None
            and receipt.configuration_sha256 == self._configuration_sha256
        ):
            raise ApplicationAuthorityResolutionError(
                "impact locator differs from its independent stage receipt"
            )
        outcomes, capability = self._inference.resolve_verified_batch(
            batch_id=receipt.binding.batch_id,
            batch_sha256=receipt.binding.batch_sha256,
        )
        reconstructed = bind_recorded_impact_inference_run(
            RecordedImpactInferenceRun(
                results=receipt.results,
                outcomes=outcomes,
                evidence_batch=capability,
            )
        )
        validated = validate_impact_results(
            reviewed,
            workload=receipt.results.workload,
            results=receipt.results,
        )
        if validated != receipt.results or reconstructed != receipt.binding:
            raise ApplicationAuthorityResolutionError(
                "impact stage receipt does not reproduce from recorded inference authority"
            )
        managed_bundle = self._optional_managed_bundle(run_id)
        if managed_bundle is not None:
            run_binding = managed_bundle.run_binding
            if type(run_binding) is not ManagedRunBindingV2 or (
                getattr(run_binding.analysis_set, "impact_evidence", None) != reconstructed
            ):
                raise ApplicationAuthorityResolutionError(
                    "managed-review impact authority differs from its stage receipt"
                )
        return reconstructed

    def _reviewed_snapshot(self, run_id: str) -> ReviewedTemporalSnapshotAuthority:
        resolver = self._source_notes_for_run(run_id)
        temporal = self._index.reopen(run_id, LifecycleEvidenceStageV1.TEMPORAL)
        owner = self._one_owner(temporal.owners, kind="temporal-analysis")
        sqlite = SqliteManagedChangeControlStore(self._state_path, secure_open=True, read_only=True)
        try:
            rows = sqlite.conn.execute(
                "SELECT target_id FROM change_control_operator_run_links "
                "WHERE run_id=? AND link_kind='temporal-review-request'",
                (run_id,),
            ).fetchall()
        finally:
            sqlite.close()
        if len(rows) != 1:
            raise ApplicationAuthorityResolutionError(
                "operator run does not locate one temporal review request"
            )
        store = SqliteChangeControlStore(self._state_path, secure_open=True, read_only=True)
        try:
            return resolve_reviewed_temporal_snapshot(
                store,
                temporal_analysis_manifest_id=owner.owner_id,
                temporal_analysis_manifest_sha256=owner.owner_sha256,
                temporal_request_id=str(rows[0]["target_id"]),
                evidence_repository=self._inference,
                source_note_resolver=resolver,
                read_only=True,
            )
        finally:
            store.close()

    def resolve_operator_revision_planning(
        self, *, run_id: str, target_id: str, target_sha256: str
    ) -> (
        ManagedRevisionPlanningAdmissionBinding
        | ManagedNoWorkPlanningAdmissionBinding
        | RevisionPlanningWorkload
    ):
        index = self._index.reopen(run_id, LifecycleEvidenceStageV1.PLANNING)
        no_work_owners = tuple(
            item for item in index.owners if item.owner_kind == "no-work-planning"
        )
        if no_work_owners:
            if len(no_work_owners) != 1:
                raise ApplicationAuthorityResolutionError(
                    "planning index contains ambiguous NO_WORK authority"
                )
            owner = no_work_owners[0]
            if owner.relative_locator is None:
                raise ApplicationAuthorityResolutionError(
                    "NO_WORK lifecycle owner omits its exact locator"
                )
            workload_sha256 = PurePosixPath(owner.relative_locator).stem
            receipt = self._no_work.reopen(
                run_id,
                f"revisionwork:{workload_sha256}",
                workload_sha256,
            )
            expected_locator = self._no_work.relative_locator(
                run_id,
                receipt.workload.workload_id,
                receipt.workload.workload_sha256,
            )
            reviewed = self._reviewed_snapshot(run_id)
            impact_exact = True
            impact_binding_id = receipt.impact_evidence_binding_id
            impact_binding_sha256 = receipt.impact_evidence_binding_sha256
            if impact_binding_id is not None or impact_binding_sha256 is not None:
                if not isinstance(impact_binding_id, str) or not isinstance(
                    impact_binding_sha256, str
                ):
                    raise ApplicationAuthorityResolutionError(
                        "NO_WORK impact authority identity is incomplete"
                    )
                impact = self.resolve_operator_impact_evidence(
                    run_id=run_id,
                    target_id=impact_binding_id,
                    target_sha256=impact_binding_sha256,
                )
                impact_stage = self._stages.reopen_impact(run_id)
                impact_exact = (
                    impact.evidence_binding_id == impact_binding_id
                    and impact.evidence_binding_sha256 == impact_binding_sha256
                    and impact_stage.results.workload.input_shards == receipt.impact_input_shards
                    and impact_stage.results.output_shards == receipt.impact_output_shards
                )
            if not (
                receipt.run_id == run_id
                and owner.owner_id == receipt.evidence_id
                and owner.owner_sha256 == receipt.evidence_sha256
                and owner.relative_locator == expected_locator
                and receipt.reviewed_snapshot_binding_id == reviewed.binding.binding_id
                and receipt.reviewed_snapshot_binding_sha256 == reviewed.binding.binding_sha256
                and impact_exact
                and self._configuration_sha256 is not None
                and receipt.configuration_sha256 == self._configuration_sha256
            ):
                raise ApplicationAuthorityResolutionError(
                    "NO_WORK receipt differs from freshly reopened upstream authority"
                )
            if target_id == receipt.workload.workload_id:
                if target_sha256 != receipt.workload.workload_sha256:
                    raise ApplicationAuthorityResolutionError(
                        "NO_WORK workload differs from its navigation link"
                    )
                return receipt.workload
            no_work_admission = bind_no_work_planning_admission(
                receipt,
                reviewed_snapshot=reviewed,
                evidence_repository=self._inference,
            )
            if not (
                target_id == no_work_admission.admission_id
                and target_sha256 == no_work_admission.admission_sha256
            ):
                raise ApplicationAuthorityResolutionError(
                    "NO_WORK admission differs from its navigation link"
                )
            managed_bundle = self._optional_managed_bundle(run_id)
            run_binding = None if managed_bundle is None else managed_bundle.run_binding
            if run_binding is not None and (
                type(run_binding) is not ManagedRunBindingV2
                or run_binding.revision_planning_admission != no_work_admission
            ):
                raise ApplicationAuthorityResolutionError(
                    "managed-review NO_WORK authority differs from its receipt"
                )
            return no_work_admission
        owner = self._one_owner(index.owners, kind="planning-stage-evidence")
        planning_receipt = self._stages.reopen_planning(run_id)
        admission = reopen_revision_planning_admission(
            planning_receipt.binding,
            reviewed_snapshot=self._reviewed_snapshot(run_id),
            evidence_repository=self._inference,
            staging_repository=self._staging,
        )
        if not (
            owner.owner_id == planning_receipt.evidence_id
            and owner.owner_sha256 == planning_receipt.evidence_sha256
            and owner.relative_locator == self._stages.relative_locator(run_id, "planning")
            and target_id == admission.admission_id
            and target_sha256 == admission.admission_sha256
        ):
            raise ApplicationAuthorityResolutionError(
                "planning locator differs from independently reopened admission authority"
            )
        managed_bundle = self._optional_managed_bundle(run_id)
        run_binding = None if managed_bundle is None else managed_bundle.run_binding
        if run_binding is not None and (
            type(run_binding) is not ManagedRunBindingV2
            or run_binding.revision_planning_admission != admission
        ):
            raise ApplicationAuthorityResolutionError(
                "managed-review planning authority differs from its stage receipt"
            )
        return admission

    def resolve_generation_zero_baseline(
        self, record: GenerationZeroBaselineStoreRecordV1
    ) -> GenerationZeroBaselineReceiptV1:
        receipt = record.baseline_receipt
        run_id = receipt.authority.run_id
        index = self._index.reopen(run_id, LifecycleEvidenceStageV1.BASELINE)
        owner = self._one_owner(index.owners, kind="generation-zero-baseline")
        run_name = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        if not (
            owner.owner_id == receipt.receipt_id
            and owner.owner_sha256 == receipt.receipt_sha256
            and owner.relative_locator == f"regression-baselines/runs/{run_name}/COMPLETE.json"
        ):
            raise ApplicationAuthorityResolutionError(
                "baseline lifecycle locator differs from its SQLite record"
            )
        capability = self._baseline.reopen(run_id)
        reopened = self._baseline.verify_capability(capability)
        if reopened != receipt:
            raise ApplicationAuthorityResolutionError(
                "baseline repository authority differs from its SQLite record"
            )
        return reopened


__all__ = [
    "ApplicationSourceNoteResolver",
    "ApplicationSourceNoteResolverLoader",
    "ApplicationAuthorityResolutionError",
    "ApplicationOperatorRunAuthorityResolver",
]
