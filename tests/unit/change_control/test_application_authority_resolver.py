from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import mastervault.change_control.application_authority_resolver as resolver_module
from mastervault.change_control.application_authority_resolver import (
    ApplicationAuthorityResolutionError,
    ApplicationOperatorRunAuthorityResolver,
)
from mastervault.change_control.application_lifecycle_evidence import (
    FilesystemLifecycleEvidenceIndex,
    LifecycleEvidenceIndexV1,
    LifecycleEvidenceOwnerV1,
    LifecycleEvidenceStageV1,
)
from mastervault.change_control.generic_incoming import (
    admit_generic_incoming_markdown_v2,
    ground_generic_extraction_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
)
from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
from mastervault.change_control.synchronous_lifecycle_store_models import (
    IncomingAdmissionIntentV1,
)

RUN_ID = f"operatorrun:{'a' * 64}"


def _incoming(tmp_path: Path) -> tuple[Path, IncomingAdmissionIntentV1]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    os.chmod(evidence_root, 0o700)
    source = tmp_path / "returns-policy-v2.md"
    source.write_text(
        """---
mastervault_change:
  schema_version: 1
  event_id: returns-event-v2
  document_id: returns-policy-v2
  document_family: returns-policy
  version_label: v2
  title: Returns Policy
  domain: customer-support
  source_type: policy
  declared_effective_from: 2026-08-20
  role: policy
  authority: primary
  operator_intent: Admit this governing document.
---
Customers receive refunds within five days.
""",
        encoding="utf-8",
    )
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    extraction = ground_generic_extraction_v2(
        admission,
        {
            "claims": [
                {
                    "quote": "Customers receive refunds within five days.",
                    "confidence": "high",
                    "affects": ["refund-policy"],
                }
            ]
        },
    )
    generic = FilesystemGenericIncomingRepositoryV2(evidence_root)
    capability = generic.persist(admission, extraction)
    bundle = generic.resolve_verified_evidence(capability).bundle
    intent = IncomingAdmissionIntentV1.create(
        operation_id="application:incoming",
        run_id=RUN_ID,
        bundle_id=bundle.bundle_id,
        bundle_sha256=bundle.bundle_sha256,
        admission_sha256=bundle.admission_sha256,
        source_receipt_sha256=bundle.source_receipt_sha256,
        projection_sha256=bundle.projection_sha256,
        inference_sha256=bundle.inference_sha256,
    )
    index = FilesystemLifecycleEvidenceIndex(evidence_root)
    index.persist(
        LifecycleEvidenceIndexV1.create(
            run_id=RUN_ID,
            stage=LifecycleEvidenceStageV1.INCOMING,
            owners=(
                LifecycleEvidenceOwnerV1(
                    owner_kind="generic-bundle",
                    owner_id=bundle.bundle_id,
                    owner_sha256=bundle.bundle_sha256,
                    relative_locator=(f"generic-incoming/v2/bundles/{bundle.bundle_sha256}.json"),
                ),
            ),
            recorded_at="2026-08-20T12:00:00+00:00",
        )
    )
    return evidence_root, intent


def test_resolver_reopens_index_and_independent_generic_repository(tmp_path: Path) -> None:
    evidence_root, intent = _incoming(tmp_path)
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=evidence_root,
        state_path=tmp_path / "unused.sqlite3",
    )

    assert resolver.resolve_incoming_source(intent).bundle_id == intent.bundle_id


def test_self_consistent_index_cannot_override_sqlite_intent_authority(tmp_path: Path) -> None:
    evidence_root, intent = _incoming(tmp_path)
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=evidence_root,
        state_path=tmp_path / "unused.sqlite3",
    )
    substituted = intent.model_copy(update={"projection_sha256": "f" * 64})

    with pytest.raises(ApplicationAuthorityResolutionError, match="repository authority"):
        resolver.resolve_incoming_source(substituted)


def test_optional_managed_bundle_distinguishes_absent_from_corrupt(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    state_path = state_root / "state.sqlite3"
    store = SqliteManagedChangeControlStore(state_path, secure_open=True)
    try:
        store.init_schema()
    finally:
        store.close()
    resolver = ApplicationOperatorRunAuthorityResolver(
        evidence_root=evidence_root,
        state_path=state_path,
    )

    assert resolver._optional_managed_bundle(RUN_ID) is None  # noqa: SLF001

    store = SqliteManagedChangeControlStore(state_path, secure_open=True)
    try:
        store.conn.execute("PRAGMA foreign_keys=OFF")
        store.conn.execute(
            "INSERT INTO change_control_managed_review_bundles VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                f"mbundle:{'1' * 64}",
                "1" * 64,
                "missing-aggregate",
                1,
                "2" * 64,
                f"mauthority:{'3' * 64}",
                0,
                f"mgeneration:{'4' * 64}",
                "5" * 64,
                '{"schema_version":1,"run_id":"not-a-bundle"}',
            ),
        )
        store.conn.commit()
    finally:
        store.close()

    with pytest.raises(ApplicationAuthorityResolutionError, match="bundle is invalid"):
        resolver._optional_managed_bundle(RUN_ID)  # noqa: SLF001


def test_impact_resolution_rejects_matching_legacy_managed_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value-equal legacy run binding cannot satisfy generic-v2 authority."""

    resolver = object.__new__(ApplicationOperatorRunAuthorityResolver)
    binding = SimpleNamespace(
        evidence_binding_id="impact:binding",
        evidence_binding_sha256="1" * 64,
        batch_id="batch:one",
        batch_sha256="2" * 64,
    )
    reviewed = SimpleNamespace(
        binding=SimpleNamespace(binding_id="reviewed:one", binding_sha256="3" * 64)
    )
    receipt = SimpleNamespace(
        evidence_id="impact-stage:one",
        evidence_sha256="4" * 64,
        binding=binding,
        reviewed_snapshot_binding_id=reviewed.binding.binding_id,
        reviewed_snapshot_binding_sha256=reviewed.binding.binding_sha256,
        configuration_sha256="5" * 64,
        results=SimpleNamespace(workload=object()),
    )
    owner = SimpleNamespace(
        owner_kind="impact-stage-evidence",
        owner_id=receipt.evidence_id,
        owner_sha256=receipt.evidence_sha256,
        relative_locator="application-stages/impact.json",
    )
    resolver._index = SimpleNamespace(reopen=lambda *_args: SimpleNamespace(owners=(owner,)))  # type: ignore[attr-defined]
    resolver._stages = SimpleNamespace(  # type: ignore[attr-defined]
        reopen_impact=lambda _run_id: receipt,
        relative_locator=lambda _run_id, _stage: owner.relative_locator,
    )
    resolver._inference = SimpleNamespace(  # type: ignore[attr-defined]
        resolve_verified_batch=lambda **_kwargs: ((), object())
    )
    resolver._configuration_sha256 = receipt.configuration_sha256  # type: ignore[attr-defined]
    resolver._reviewed_snapshot = lambda _run_id: reviewed  # type: ignore[method-assign]
    legacy_run = SimpleNamespace(analysis_set=SimpleNamespace(impact_evidence=binding))
    resolver._optional_managed_bundle = lambda _run_id: SimpleNamespace(  # type: ignore[method-assign]
        run_binding=legacy_run
    )
    monkeypatch.setattr(resolver_module, "RecordedImpactInferenceRun", lambda **kwargs: kwargs)
    monkeypatch.setattr(resolver_module, "bind_recorded_impact_inference_run", lambda _run: binding)
    monkeypatch.setattr(
        resolver_module, "validate_impact_results", lambda *_args, **_kwargs: receipt.results
    )

    with pytest.raises(ApplicationAuthorityResolutionError, match="managed-review impact"):
        resolver.resolve_operator_impact_evidence(
            run_id=RUN_ID,
            target_id=binding.evidence_binding_id,
            target_sha256=binding.evidence_binding_sha256,
        )


def test_planning_resolution_rejects_matching_legacy_managed_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning navigation also requires the exact generic-v2 run-binding type."""

    resolver = object.__new__(ApplicationOperatorRunAuthorityResolver)
    admission = SimpleNamespace(admission_id="planning:one", admission_sha256="6" * 64)
    receipt = SimpleNamespace(
        evidence_id="planning-stage:one",
        evidence_sha256="7" * 64,
        binding=object(),
    )
    owner = SimpleNamespace(
        owner_kind="planning-stage-evidence",
        owner_id=receipt.evidence_id,
        owner_sha256=receipt.evidence_sha256,
        relative_locator="application-stages/planning.json",
    )
    resolver._index = SimpleNamespace(reopen=lambda *_args: SimpleNamespace(owners=(owner,)))  # type: ignore[attr-defined]
    resolver._stages = SimpleNamespace(  # type: ignore[attr-defined]
        reopen_planning=lambda _run_id: receipt,
        relative_locator=lambda _run_id, _stage: owner.relative_locator,
    )
    resolver._inference = object()  # type: ignore[attr-defined]
    resolver._staging = object()  # type: ignore[attr-defined]
    resolver._reviewed_snapshot = lambda _run_id: object()  # type: ignore[method-assign]
    legacy_run = SimpleNamespace(revision_planning_admission=admission)
    resolver._optional_managed_bundle = lambda _run_id: SimpleNamespace(  # type: ignore[method-assign]
        run_binding=legacy_run
    )
    monkeypatch.setattr(
        resolver_module,
        "reopen_revision_planning_admission",
        lambda *_args, **_kwargs: admission,
    )

    with pytest.raises(ApplicationAuthorityResolutionError, match="managed-review planning"):
        resolver.resolve_operator_revision_planning(
            run_id=RUN_ID,
            target_id=admission.admission_id,
            target_sha256=admission.admission_sha256,
        )
