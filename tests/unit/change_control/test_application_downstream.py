from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from mastervault.change_control import application_downstream as downstream
from mastervault.change_control.application_errors import (
    ChangeControlApplicationConflictError,
    ChangeControlApplicationIntegrityError,
)
from mastervault.change_control.application_read_models import ApplicationReadModels
from mastervault.change_control.change_application_contracts import (
    ChangeReviewPacketV1,
    ChangeReviewStageV1,
    ChangeReviewSubjectKindV1,
    ChangeReviewSubjectV1,
    TemporalReviewChoiceV1,
    TemporalReviewDecisionDocumentV1,
    TemporalReviewDecisionItemV1,
)
from mastervault.change_control.generic_governing_source import CompositeManagedReviewResolverV2
from mastervault.change_control.managed_review import ManagedNoWorkPlanningAdmissionBinding
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.operator_run import (
    OperatorRunCommand,
    OperatorRunLinkCommand,
    OperatorRunLinkKind,
    OperatorRunLinkRecord,
    OperatorRunRecord,
    OperatorRunView,
)
from mastervault.change_control.review import ReviewDisposition, ReviewSubjectKind
from mastervault.models import content_hash
from mastervault.providers import MockEmbedding
from mastervault.providers.llm import LLMResult
from mastervault.storage.sqlite import SqliteBackend
from mastervault.sync.indexer import sync_vault

SHA_A = "a" * 64
SHA_B = "b" * 64
RUN_ID = f"operatorrun:{'1' * 64}"
REQUEST_ID = f"reviewreq:{'2' * 64}"
BOOTSTRAP_OPERATION = "workspace-bootstrap:downstream-generic-v1"


def _incoming_source(path: Path) -> Path:
    source = path / "returns-policy-v2.md"
    source.write_text(
        "---\nmastervault_change:\n"
        "  schema_version: 1\n"
        "  event_id: returns-event-v2\n"
        "  document_id: returns-policy-v2\n"
        "  document_family: returns-policy\n"
        "  version_label: v2\n"
        "  title: Returns Policy V2\n"
        "  domain: customer-support\n"
        "  source_type: policy\n"
        "  declared_effective_from: 2026-08-20\n"
        "  role: policy\n"
        "  authority: primary\n"
        "  operator_intent: Adopt the successor policy.\n"
        "---\nCustomers must present a receipt for every return.\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    return source


def _regression_suite(path: Path) -> Path:
    suite = path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_id": "returns-regression",
                "suite_version": 1,
                "cases": [
                    {
                        "case_id": "control-search",
                        "role": "control",
                        "kind": "search",
                        "query": "unrelated account topic",
                        "k": 1,
                        "record_types": ["claim"],
                        "rerank": False,
                    },
                    {
                        "case_id": "target-search",
                        "role": "targeted",
                        "kind": "search",
                        "query": "returns receipt",
                        "domain": "customer-support",
                        "k": 2,
                        "record_types": ["claim"],
                        "rerank": False,
                    },
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    suite.chmod(0o600)
    return suite


def _operator_workspace(
    tmp_path: Path,
    *,
    include_support_guide: bool = True,
    include_training_guide: bool = False,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    vault = workspace / "vault" / "customer-support" / "sources"
    raw_root = workspace / "raw"
    vault.mkdir(parents=True)
    raw_root.mkdir()
    specifications: tuple[tuple[str, str, str, str, str, str, str, str], ...] = (
        (
            "policy.md",
            "policy-returns.md",
            "returns-policy",
            "returns-policy",
            "policy",
            "Returns Policy",
            "Customers may return standard items within thirty days of delivery.",
            "The governing policy allows standard returns within thirty days.",
        ),
        (
            "support-guide.md",
            "support-guide.md",
            "returns-support-guide",
            "returns-support-guide",
            "faq",
            "Returns Support Guide",
            "Support agents ask customers for a receipt when processing returns.",
            "Support agents ask customers for a receipt when processing returns.",
        ),
        (
            "training-guide.md",
            "training-guide.md",
            "returns-training-guide",
            "returns-training-guide",
            "sop",
            "Returns Training Guide",
            "Training materials tell agents to request a receipt for returns.",
            "Training materials tell agents to request a receipt for returns.",
        ),
    )
    if include_training_guide:
        pass
    elif include_support_guide:
        specifications = specifications[:2]
    else:
        specifications = specifications[:1]
    entries: list[dict[str, Any]] = []
    for note_name, raw_name, document_id, family, role, title, claim, raw_text in specifications:
        raw_bytes = f"{raw_text}\n".encode()
        raw_path = raw_root / raw_name
        raw_path.write_bytes(raw_bytes)
        logical = f"customer-support/sources/{note_name}"
        raw_relative = f"raw/{raw_name}"
        raw_evidence_path = "bootstrap-sources/workspace/" + hashlib.sha256(raw_bytes).hexdigest()
        note_text = (
            "---\n"
            "domain: customer-support\n"
            "type: source\n"
            f"source_type: {role}\n"
            f"title: {title}\n"
            "tags: [returns, receipts]\n"
            "status: processed\n"
            "created: 2026-03-01\n"
            "updated: 2026-03-01\n"
            f"provenance: {raw_evidence_path}\n"
            f"provenance_hash: {content_hash(raw_bytes.decode())}\n"
            "key_claims:\n"
            f"  - id: {document_id}-01\n"
            f"    statement: {claim}\n"
            "    confidence: high\n"
            "    affects: [returns]\n"
            "---\n\n"
            f"# {title}\n\n{raw_text}\n"
        )
        note_bytes = note_text.encode()
        (vault / note_name).write_bytes(note_bytes)
        entries.append(
            {
                "logical_path": logical,
                "source_note_sha256": hashlib.sha256(note_bytes).hexdigest(),
                "source_note_byte_count": len(note_bytes),
                "source_root_id": "workspace",
                "source_relative_path": raw_relative,
                "source_note_provenance": raw_evidence_path,
                "raw_source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "raw_source_byte_count": len(raw_bytes),
                "document_id": document_id,
                "document_family": family,
                "version_label": "v1",
                "declared_effective_from": "2026-03-01",
                "declared_effective_to": None,
                "role": role,
                "authority": "primary",
            }
        )
    embedder = MockEmbedding()
    index_path = workspace / "index.db"
    backend = SqliteBackend(index_path)
    try:
        backend.init_schema(embedder.dimensions, embedder.model_version)
        sync_vault(workspace / "vault", backend, embedder, full=True)
    finally:
        backend.close()
    index_bytes = index_path.read_bytes()
    manifest = workspace / "bootstrap.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "aggregate_id": "downstream-generic-workspace",
                "legacy_index_file_sha256": hashlib.sha256(index_bytes).hexdigest(),
                "legacy_index_file_byte_count": len(index_bytes),
                "managed_source_notes": entries,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return workspace, manifest


class _AdoptionOnlyLifecycleLLM:
    def __init__(self, *, downstream_dependency: bool = True, mixed_impact: bool = False) -> None:
        self.calls: list[str] = []
        self.downstream_dependency = downstream_dependency
        self.mixed_impact = mixed_impact

    def complete(self, task: str, prompt: str, **kwargs: Any) -> LLMResult:
        del kwargs
        self.calls.append(task)
        if task == "generic_grounded_claim_extraction_v2":
            payload: dict[str, Any] = {
                "claims": [
                    {
                        "quote": "Customers must present a receipt for every return.",
                        "confidence": "high",
                        "affects": ["returns"],
                    }
                ]
            }
            model = "mock-small"
        else:
            request = json.loads(prompt)
            shard = json.loads(request["input_shard_utf8"])
            if task == "classification":
                payload = {
                    "schema_version": 1,
                    "task": "classification",
                    "decisions": [
                        {
                            "pair_id": item["candidate"]["pair_id"],
                            "disposition": (
                                "SUPERSEDES"
                                if item["candidate"]["changed_document_family"]
                                == item["candidate"]["incumbent_document_family"]
                                else "COEXISTS"
                            ),
                            "newer_revision_id": (
                                item["candidate"]["changed_claim_revision_id"]
                                if item["candidate"]["changed_document_family"]
                                == item["candidate"]["incumbent_document_family"]
                                else None
                            ),
                            "rationale": "The incoming governing claim supersedes the prior one.",
                            "confidence": 0.99,
                        }
                        for item in shard["pairs"]
                    ],
                }
            elif task == "dependency":
                note = shard["downstream_note"]["source_note_utf8"]
                body_start = shard["downstream_note"]["body_start_char"]
                start = next(
                    index for index in range(body_start, len(note)) if note[index].isalpha()
                )
                payload = {
                    "schema_version": 1,
                    "task": "dependency",
                    "decisions": [
                        {
                            "candidate_id": item["candidate_id"],
                            "disposition": (
                                "DEPENDS_ON" if self.downstream_dependency else "NOT_DEPENDENT"
                            ),
                            "dependency_kind": (
                                "summarizes" if self.downstream_dependency else None
                            ),
                            "selected_downstream_claim_revision_ids": [],
                            "spans": (
                                [{"start_char": start, "end_char": start + 1}]
                                if self.downstream_dependency
                                else []
                            ),
                            "rationale": (
                                "The downstream note summarizes the governing policy."
                                if self.downstream_dependency
                                else "The downstream note is independent of this policy."
                            ),
                            "confidence": 0.99,
                        }
                        for item in shard["candidates"]
                    ],
                }
            elif task == "impact":
                note = shard["target_note"]["source_note_utf8"]
                body_start = shard["target_note"]["body_start_char"]
                evidence_start = next(
                    index for index in range(body_start, len(note)) if note[index].isalpha()
                )
                payload = {
                    "schema_version": 1,
                    "task": "impact",
                    "decisions": [
                        {
                            "question_id": item["question_id"],
                            "disposition": (
                                "AFFECTED"
                                if self.mixed_impact
                                and item["target_document"]["document_id"]
                                == "returns-support-guide"
                                else "NO_CHANGE_REQUIRED"
                            ),
                            "spans": (
                                [
                                    {
                                        "start_char": evidence_start,
                                        "end_char": evidence_start + 1,
                                    }
                                ]
                                if self.mixed_impact
                                and item["target_document"]["document_id"]
                                == "returns-support-guide"
                                else []
                            ),
                            "attention_path_context_ids": [
                                value["path_id"] for value in item["attention_paths"]
                            ],
                            "dependency_context_ids": [
                                value["dependency_id"] for value in item["existing_dependencies"]
                            ],
                            "rationale": "The current downstream wording remains valid.",
                        }
                        for item in shard["questions"]
                    ],
                }
            else:
                target = shard["target"]
                selector = (
                    "governing-evidence"
                    if target["required_response_kind"] == "affected-revision"
                    else "target-evidence"
                )
                citation = next(
                    item
                    for item in shard["citation_inputs"]["inputs"]
                    if item["input_selector"] == selector
                )
                text = citation["text_utf8"]
                start = next(index for index, value in enumerate(text) if not value.isspace())
                if target["required_response_kind"] == "affected-revision":
                    raw = shard["predecessor_raw_utf8"]
                    edit_start = raw.index("Support")
                    claim = shard["existing_claims"][0]
                    payload = {
                        "kind": "affected-revision",
                        "target_key": target["target_key"],
                        "question_ids": target["question_ids"],
                        "edits": [
                            {
                                "start_char": edit_start,
                                "end_char": edit_start + len("Support"),
                                "replacement_text": "Service",
                                "citations": [
                                    {
                                        "input_selector": selector,
                                        "start_char": start,
                                        "end_char": start + 1,
                                    }
                                ],
                            }
                        ],
                        "source_claim_statement_rewrites": [
                            {
                                "source_claim_id": claim["source_claim_id"],
                                "replacement_statement": claim["statement"].replace(
                                    "Support", "Service", 1
                                ),
                                "edit_ordinals": [0],
                            }
                        ],
                        "rationale": "Revise the exact affected support guidance.",
                    }
                else:
                    payload = {
                        "kind": "no-change",
                        "target_key": target["target_key"],
                        "question_ids": target["question_ids"],
                        "citations": [
                            {
                                "input_selector": selector,
                                "start_char": start,
                                "end_char": start + 1,
                            }
                        ],
                        "rationale": "No downstream revision is required for this exact target.",
                    }
            model = "mock-medium"
        return LLMResult(
            text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            parsed=None,
            request_id=f"adoption-only:{len(self.calls)}",
            model=model,
            usage_in=10,
            usage_out=10,
            cost_usd=0.0,
        )


def _document(*, request_sha256: str = SHA_A) -> TemporalReviewDecisionDocumentV1:
    return TemporalReviewDecisionDocumentV1.create(
        run_id=RUN_ID,
        request_id=REQUEST_ID,
        request_sha256=request_sha256,
        operation_id="downstream-test:temporal-decision",
        reviewer_id="reviewer.test",
        rationale="Reject this exact reviewed temporal proposal.",
        decisions=(
            TemporalReviewDecisionItemV1(
                subject_id=f"rel:{'3' * 64}",
                subject_sha256=SHA_B,
                subject_kind=ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT,
                choice=TemporalReviewChoiceV1.REJECT,
            ),
        ),
    )


def _packet() -> ChangeReviewPacketV1:
    return ChangeReviewPacketV1(
        run_id=RUN_ID,
        stage=ChangeReviewStageV1.TEMPORAL,
        request_id=REQUEST_ID,
        request_sha256=SHA_A,
        subjects=(
            ChangeReviewSubjectV1(
                subject_id=f"rel:{'3' * 64}",
                subject_sha256=SHA_B,
                subject_kind=ChangeReviewSubjectKindV1.DOCUMENT_REPLACEMENT,
                statement=f"docv:{'4' * 64} supersedes docv:{'5' * 64}",
                rationale="The exact incoming version supersedes its predecessor.",
            ),
        ),
    )


def test_exact_packet_rejects_stale_request_before_any_write() -> None:
    reads = SimpleNamespace(get_change_review=lambda _run_id: _packet())

    with pytest.raises(ChangeControlApplicationConflictError, match="current exact"):
        downstream._require_exact_packet(reads, _document(request_sha256="c" * 64))


def test_full_temporal_rejection_records_exact_human_inputs_then_links_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    recorded: dict[str, Any] = {}

    class Store:
        def get_review_request(self, request_id: str) -> Any:
            assert request_id == REQUEST_ID
            return SimpleNamespace(request=SimpleNamespace(request_payload_sha256=SHA_A))

        def decide_review(self, command: Any, *, operation_id: str) -> Any:
            recorded["command"] = command
            recorded["operation_id"] = operation_id
            return SimpleNamespace(
                decision=SimpleNamespace(
                    request_id=REQUEST_ID,
                    decision_payload_sha256="d" * 64,
                )
            )

    monkeypatch.setattr(
        downstream,
        "_link",
        lambda **values: recorded.setdefault("link", values),
    )
    downstream._record_temporal(
        store=Store(),  # type: ignore[arg-type]
        resolver=object(),
        document=document,
    )

    command = recorded["command"]
    assert recorded["operation_id"] == document.operation_id
    assert command.reviewer_id == document.reviewer_id
    assert command.rationale == document.rationale
    assert len(command.items) == 1
    assert command.items[0].kind == ReviewSubjectKind.DOCUMENT_REPLACEMENT
    assert command.items[0].original_subject_sha256 == SHA_B
    assert command.items[0].disposition == ReviewDisposition.REJECTED
    assert recorded["link"]["kind"] == OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION
    assert recorded["link"]["target_id"] == REQUEST_ID
    assert recorded["link"]["target_sha256"] == "d" * 64


def test_temporal_decision_failure_has_no_navigation_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linked = False

    class Store:
        def get_review_request(self, _request_id: str) -> Any:
            return SimpleNamespace(request=SimpleNamespace(request_payload_sha256=SHA_A))

        def decide_review(self, _command: Any, *, operation_id: str) -> Any:
            del operation_id
            raise RuntimeError("pre-CAS failure")

    def link(**_values: Any) -> None:
        nonlocal linked
        linked = True

    monkeypatch.setattr(downstream, "_link", link)
    with pytest.raises(RuntimeError, match="pre-CAS"):
        downstream._record_temporal(
            store=Store(),  # type: ignore[arg-type]
            resolver=object(),
            document=_document(),
        )
    assert not linked


def test_navigation_preflight_closes_reader_before_returning_to_writer_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False
    run = SimpleNamespace(links=())

    class Reader:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def get_operator_run(self, run_id: str, *, resolver: Any) -> Any:
            assert run_id == RUN_ID
            assert resolver is navigation
            return run

        def close(self) -> None:
            nonlocal closed
            closed = True

    navigation = object()
    monkeypatch.setattr(downstream, "SqliteManagedChangeControlStore", Reader)

    reopened, prepared = downstream._prepare_navigation(
        state_path=SimpleNamespace(),  # type: ignore[arg-type]
        run_id=RUN_ID,
        resolver=navigation,
    )

    assert reopened is run
    assert isinstance(prepared, downstream._PreparedNavigationResolver)
    assert closed


def test_composite_resolver_dispatches_exact_no_work_admission() -> None:
    binding = ManagedNoWorkPlanningAdmissionBinding.model_construct()

    class Sealed:
        def resolve_revision_planning_admission(self, value: Any) -> Any:
            assert value is binding
            return value

    composite = object.__new__(CompositeManagedReviewResolverV2)
    object.__setattr__(composite, "sealed", Sealed())
    object.__setattr__(composite, "generic", SimpleNamespace())
    assert composite.resolve_revision_planning_admission(binding) is binding


def test_workspace_projection_authority_is_exact_and_sealed_behavior_is_unchanged(
    tmp_path: Path,
) -> None:
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.generic_governing_source import (
        GenericGoverningSourceResolverV2,
        WorkspaceSourceNoteProjectionAuthority,
    )
    from mastervault.change_control.managed_review import (
        ManagedArtifactKind,
        ManagedArtifactRef,
        SourceNoteProjectionBinding,
    )
    from mastervault.change_control.managed_review_repository import (
        RepositoryBackedManagedReviewResolver,
    )
    from mastervault.change_control.managed_source_note import (
        MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
        MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
    )
    from mastervault.change_control.models import canonical_json_bytes
    from mastervault.config import Settings

    workspace, manifest = _operator_workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    ChangeControlApplication(settings).bootstrap(manifest, BOOTSTRAP_OPERATION)
    settings.paths.change_control_evidence_root.mkdir(parents=True, mode=0o700)
    with downstream._write_runtime(settings) as (_app, _paths, _resolver, _runtime, resolved):
        authorities = []
        for item in resolved.managed_source_notes:
            raw_bytes = item.raw_source_bytes
            note_bytes = item.snapshot.source_note_utf8.encode("utf-8")
            authorities.append(
                WorkspaceSourceNoteProjectionAuthority(
                    metadata=item.metadata,
                    snapshot=item.snapshot,
                    raw_artifact=ManagedArtifactRef.create(
                        kind=ManagedArtifactKind.RAW_SOURCE,
                        path=item.snapshot.document.source_path,
                        sha256=hashlib.sha256(raw_bytes).hexdigest(),
                        byte_count=len(raw_bytes),
                    ),
                    raw_bytes=raw_bytes,
                    note_artifact=ManagedArtifactRef.create(
                        kind=ManagedArtifactKind.SOURCE_NOTE,
                        path=item.snapshot.source_note_path,
                        sha256=hashlib.sha256(note_bytes).hexdigest(),
                        byte_count=len(note_bytes),
                    ),
                    note_bytes=note_bytes,
                    projected_claims=tuple(
                        claim
                        for claim in resolved.aggregate.claims.revisions
                        if claim.document == item.snapshot.document
                    ),
                )
            )
    authority = authorities[0]
    report = canonical_json_bytes(
        {
            "namespace": "mastervault.managed-source-note-projection-validation.v1",
            "validator_version": MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
            "source_note_schema_sha256": MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
            "raw": authority.raw_artifact.model_dump(mode="json"),
            "note": authority.note_artifact.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in authority.projected_claims],
        }
    )
    projection = SourceNoteProjectionBinding.create_from_validator_output(
        raw_artifact=authority.raw_artifact,
        note_artifact=authority.note_artifact,
        canonical_raw_path=authority.raw_artifact.path,
        canonical_note_path=authority.note_artifact.path,
        validator_version=MANAGED_SOURCE_NOTE_VALIDATOR_VERSION,
        source_note_schema_sha256=MANAGED_SOURCE_NOTE_SCHEMA_SHA256,
        validator_result_sha256=hashlib.sha256(report).hexdigest(),
        projected_claims=authority.projected_claims,
    )
    sealed = object.__new__(RepositoryBackedManagedReviewResolver)
    generic = object.__new__(GenericGoverningSourceResolverV2)
    composite = CompositeManagedReviewResolverV2(
        sealed=sealed,
        generic=generic,
        workspace_projection_authorities=(authority,),
    )
    assert (
        composite.verify_source_note_projection(
            projection,
            raw_bytes=authority.raw_bytes,
            note_bytes=authority.note_bytes,
        )
        == projection
    )
    with pytest.raises(ValueError, match="provenance"):
        sealed.verify_source_note_projection(
            projection,
            raw_bytes=authority.raw_bytes,
            note_bytes=authority.note_bytes,
        )
    with pytest.raises(ValueError, match="guarded bootstrap"):
        WorkspaceSourceNoteProjectionAuthority(
            **{
                **authority.__dict__,
                "metadata": authority.metadata.model_copy(
                    update={"source_note_provenance": "arbitrary.md"}
                ),
            }
        )
    with pytest.raises(ValueError, match="guarded bootstrap"):
        WorkspaceSourceNoteProjectionAuthority(
            **{**authority.__dict__, "note_artifact": authorities[1].note_artifact}
        )
    with pytest.raises(ValueError, match="crosswire"):
        CompositeManagedReviewResolverV2(
            sealed=sealed,
            generic=generic,
            workspace_projection_authorities=(authority, authority),
        )


def test_completed_no_op_shape_allows_work_receipt_but_no_managed_authority() -> None:
    run_command = OperatorRunCommand.create(
        operation_id="downstream-test:no-work-run",
        aggregate_id="downstream-test",
        base_authority_id=f"mauthority:{'6' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="7" * 64,
    )
    kinds = (
        OperatorRunLinkKind.BOOTSTRAP_INTENT,
        OperatorRunLinkKind.WORKSPACE_INVENTORY,
        OperatorRunLinkKind.LEGACY_INDEX_READINESS,
        OperatorRunLinkKind.GENERATION_ZERO_AUTHORITY,
        OperatorRunLinkKind.INCOMING_SOURCE,
        OperatorRunLinkKind.REGRESSION_SUITE,
        OperatorRunLinkKind.GENERATION_ZERO_BASELINE,
        OperatorRunLinkKind.TEMPORAL_PROPOSAL,
        OperatorRunLinkKind.TEMPORAL_REVIEW_REQUEST,
        OperatorRunLinkKind.TEMPORAL_REVIEW_DECISION,
        OperatorRunLinkKind.REVISION_PLANNING,
    )
    run = OperatorRunView(
        record=OperatorRunRecord(
            command=run_command,
            created_at="2026-08-20T12:00:00+00:00",
        ),
        links=tuple(
            OperatorRunLinkRecord(
                command=OperatorRunLinkCommand.create(
                    operation_id=f"downstream-test:no-work-link-{index}",
                    run_id=run_command.run_id,
                    kind=kind,
                    target_id=f"target:{index}",
                    target_sha256=f"{index + 1:x}" * 64,
                ),
                sequence=index,
                recorded_at="2026-08-20T12:00:00+00:00",
            )
            for index, kind in enumerate(kinds)
        ),
    )

    ApplicationReadModels._require_shape(run, downstream.ChangeRunPhaseV1.COMPLETED_NO_OP)

    managed = OperatorRunLinkRecord(
        command=OperatorRunLinkCommand.create(
            operation_id="downstream-test:surplus-managed-request",
            run_id=run_command.run_id,
            kind=OperatorRunLinkKind.MANAGED_REVIEW_REQUEST,
            target_id="target:managed",
            target_sha256="f" * 64,
        ),
        sequence=len(run.links),
        recorded_at="2026-08-20T12:00:00+00:00",
    )
    with pytest.raises(ValueError, match="surplus"):
        ApplicationReadModels._require_shape(
            OperatorRunView(record=run.record, links=(*run.links, managed)),
            downstream.ChangeRunPhaseV1.COMPLETED_NO_OP,
        )


def test_real_operator_temporal_full_rejection_is_terminal_and_provider_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mastervault.change_control.application_generic_extraction as extraction_module
    import mastervault.change_control.application_provider_bridge as bridge_module
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.change_application_contracts import (
        ActivateChangeRequestV1,
        ChangeExecutionModeV1,
        ChangeRunPhaseV1,
        StartChangeRequestV1,
    )
    from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
    from mastervault.config import Settings
    from mastervault.models import Domain

    workspace, manifest = _operator_workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {"bootstrap_manifest": manifest},
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    llm = _AdoptionOnlyLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    started = application.start_change(
        StartChangeRequestV1(
            operation_id="downstream-test:real-reject-start",
            source=_incoming_source(tmp_path),
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=_regression_suite(tmp_path),
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    packet = application.get_change_review(started.run_id)
    before = len(llm.calls)
    decision = TemporalReviewDecisionDocumentV1.create(
        run_id=packet.run_id,
        request_id=packet.request_id,
        request_sha256=packet.request_sha256,
        operation_id="downstream-test:real-reject-decision",
        reviewer_id="reviewer.operator",
        rationale="Reject every exact temporal subject.",
        decisions=tuple(
            TemporalReviewDecisionItemV1(
                subject_id=item.subject_id,
                subject_sha256=item.subject_sha256,
                subject_kind=cast(Any, item.subject_kind),
                choice=TemporalReviewChoiceV1.REJECT,
            )
            for item in packet.subjects
        ),
    )

    first = downstream.record_change_review(settings=settings, document=decision)
    replay = downstream.record_change_review(settings=settings, document=decision)

    assert first == replay
    assert first.phase == ChangeRunPhaseV1.REJECTED_NO_OP
    assert len(llm.calls) == before
    no_op_request = ActivateChangeRequestV1(
        run_id=started.run_id,
        operation_id="downstream-test:real-reject-activate-no-op",
    )
    no_op_result = downstream.activate_change(settings=settings, request=no_op_request)
    assert downstream.activate_change(settings=settings, request=no_op_request) == no_op_result
    assert no_op_result.phase == ChangeRunPhaseV1.REJECTED_NO_OP
    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        operation = store.conn.execute(
            "SELECT operation_kind,run_id FROM synchronous_application_operations "
            "WHERE operation_id=?",
            (no_op_request.operation_id,),
        ).fetchone()
        activation_effects = store.conn.execute(
            "SELECT (SELECT count(*) FROM change_control_revision_publication_events) + "
            "(SELECT count(*) FROM change_control_index_generation_receipts) + "
            "(SELECT count(*) FROM change_control_generation_activation_receipts)"
        ).fetchone()[0]
    finally:
        store.close()
    assert tuple(operation) == ("activate-no-op", started.run_id)
    assert activation_effects == 0


@pytest.mark.parametrize(
    ("choice_value", "expected_phase"),
    (("adopt", "ready-to-activate"), ("reject", "rejected-no-op")),
)
def test_real_operator_zero_target_adoption_review_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice_value: str,
    expected_phase: str,
) -> None:
    import mastervault.change_control.application_generic_extraction as extraction_module
    import mastervault.change_control.application_provider_bridge as bridge_module
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.change_application_contracts import (
        ActivateChangeRequestV1,
        ChangeExecutionModeV1,
        ChangeRunPhaseV1,
        ManagedAdoptionChoiceV1,
        ManagedReviewDecisionDocumentV1,
        StartChangeRequestV1,
    )
    from mastervault.config import Settings
    from mastervault.models import Domain

    workspace, manifest = _operator_workspace(tmp_path, include_support_guide=False)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    llm = _AdoptionOnlyLifecycleLLM(downstream_dependency=False)
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    started = application.start_change(
        StartChangeRequestV1(
            operation_id=f"downstream-test:zero-target-start-{choice_value}",
            source=_incoming_source(tmp_path),
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=_regression_suite(tmp_path),
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    with pytest.raises(
        downstream.ChangeControlApplicationReviewRequiredError,
        match="requires a human review decision",
    ):
        downstream.activate_change(
            settings=settings,
            request=ActivateChangeRequestV1(
                run_id=started.run_id,
                operation_id="downstream-test:pending-review-activate",
            ),
        )
    temporal = application.get_change_review(started.run_id)
    awaiting = downstream.record_change_review(
        settings=settings,
        document=TemporalReviewDecisionDocumentV1.create(
            run_id=temporal.run_id,
            request_id=temporal.request_id,
            request_sha256=temporal.request_sha256,
            operation_id=f"downstream-test:zero-target-temporal-{choice_value}",
            reviewer_id="reviewer.operator",
            rationale="Accept the governing source for explicit adoption review.",
            decisions=tuple(
                TemporalReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=TemporalReviewChoiceV1.ACCEPT,
                )
                for item in temporal.subjects
            ),
        ),
    )
    assert awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    managed = application.get_change_review(started.run_id)
    assert managed.subjects == ()
    assert managed.adoption_only is True
    assert managed.governing_source_adoption_id == (
        f"mgoverningsource:{managed.governing_source_adoption_sha256}"
    )
    before = len(llm.calls)
    choice = ManagedAdoptionChoiceV1(choice_value)
    result = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=managed.run_id,
            request_id=managed.request_id,
            request_sha256=managed.request_sha256,
            operation_id=f"downstream-test:zero-target-managed-{choice_value}",
            reviewer_id="reviewer.operator",
            rationale="Record the exact governing-source adoption choice.",
            decisions=(),
            adoption_choice=choice,
        ),
    )
    assert result.phase.value == expected_phase
    assert len(llm.calls) == before
    replay = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=managed.run_id,
            request_id=managed.request_id,
            request_sha256=managed.request_sha256,
            operation_id=f"downstream-test:zero-target-managed-{choice_value}",
            reviewer_id="reviewer.operator",
            rationale="Record the exact governing-source adoption choice.",
            decisions=(),
            adoption_choice=choice,
        ),
    )
    assert replay == result
    assert len(llm.calls) == before
    if choice == ManagedAdoptionChoiceV1.ADOPT:
        activated = downstream.activate_change(
            settings=settings,
            request=ActivateChangeRequestV1(
                run_id=started.run_id,
                operation_id="downstream-test:zero-target-activate",
            ),
        )
        assert activated.phase == ChangeRunPhaseV1.ACTIVATED


def test_real_operator_adoption_only_activation_cas_replay_and_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mastervault.change_control.application_generic_extraction as extraction_module
    import mastervault.change_control.application_provider_bridge as bridge_module
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.change_application_contracts import (
        ActivateChangeRequestV1,
        ChangeExecutionModeV1,
        ChangeRunPhaseV1,
        ManagedReviewChoiceV1,
        ManagedReviewDecisionDocumentV1,
        ManagedReviewDecisionItemV1,
        StartChangeRequestV1,
    )
    from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
    from mastervault.change_control.query_generation import (
        QueryGenerationKind,
        QueryGenerationSelector,
    )
    from mastervault.change_control.regression_baseline import (
        GenerationZeroBaselineRepository,
    )
    from mastervault.config import Settings
    from mastervault.models import Domain

    workspace, manifest = _operator_workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    llm = _AdoptionOnlyLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    started = application.start_change(
        StartChangeRequestV1(
            operation_id="downstream-test:adoption-start",
            source=_incoming_source(tmp_path),
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=_regression_suite(tmp_path),
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    temporal = application.get_change_review(started.run_id)
    temporal_document = TemporalReviewDecisionDocumentV1.create(
        run_id=temporal.run_id,
        request_id=temporal.request_id,
        request_sha256=temporal.request_sha256,
        operation_id="downstream-test:adoption-temporal",
        reviewer_id="reviewer.operator",
        rationale="Accept every exact temporal subject.",
        decisions=tuple(
            TemporalReviewDecisionItemV1(
                subject_id=item.subject_id,
                subject_sha256=item.subject_sha256,
                subject_kind=cast(Any, item.subject_kind),
                choice=TemporalReviewChoiceV1.ACCEPT,
            )
            for item in temporal.subjects
        ),
    )
    awaiting = downstream.record_change_review(settings=settings, document=temporal_document)
    assert awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    before_temporal_retry = len(llm.calls)
    assert (
        downstream.record_change_review(settings=settings, document=temporal_document) == awaiting
    )
    assert len(llm.calls) == before_temporal_retry
    managed = application.get_change_review(started.run_id)
    assert all(item.subject_kind.value == "no-change-impact-card" for item in managed.subjects)
    ready = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=managed.run_id,
            request_id=managed.request_id,
            request_sha256=managed.request_sha256,
            operation_id="downstream-test:adoption-managed",
            reviewer_id="reviewer.operator",
            rationale="Confirm every exact evidence-backed no-change result.",
            decisions=tuple(
                ManagedReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=ManagedReviewChoiceV1.CONFIRM_NO_CHANGE,
                )
                for item in managed.subjects
            ),
        ),
    )
    assert ready.phase == ChangeRunPhaseV1.READY_TO_ACTIVATE
    activation_request = ActivateChangeRequestV1(
        run_id=started.run_id,
        operation_id="downstream-test:adoption-activate",
    )
    baseline_repository = GenerationZeroBaselineRepository(
        settings.paths.change_control_evidence_root
    )
    baseline_receipt = baseline_repository.open(started.run_id)
    baseline_complete_path = (
        settings.paths.change_control_evidence_root / baseline_receipt.replay_ref.relative_locator
    )
    baseline_complete_bytes = baseline_complete_path.read_bytes()
    baseline_complete_path.write_bytes(baseline_complete_bytes + b"\n")
    with pytest.raises(ChangeControlApplicationIntegrityError):
        downstream.activate_change(settings=settings, request=activation_request)
    baseline_complete_path.write_bytes(baseline_complete_bytes)
    baseline_complete_path.chmod(0o600)
    assert application.get_change_status(started.run_id).phase == (
        ChangeRunPhaseV1.READY_TO_ACTIVATE
    )

    def interrupt_before_cas(boundary: str) -> None:
        if boundary == "before-authority-cas":
            raise RuntimeError("simulated pre-CAS process interruption")

    with pytest.raises(RuntimeError) as interrupted:
        downstream.activate_change(
            settings=settings,
            request=activation_request,
            failure_hook=interrupt_before_cas,
        )
    assert str(interrupted.value) == "simulated pre-CAS process interruption"
    assert application.get_change_status(started.run_id).phase == (
        ChangeRunPhaseV1.READY_TO_ACTIVATE
    )

    def interrupt_after_authority_update(boundary: str) -> None:
        if boundary == "authority-updated-before-receipt":
            raise RuntimeError("simulated post-CAS receipt interruption")

    with pytest.raises(RuntimeError) as post_cas:
        downstream.activate_change(
            settings=settings,
            request=activation_request,
            failure_hook=interrupt_after_authority_update,
        )
    assert str(post_cas.value) == "simulated post-CAS receipt interruption"
    assert application.get_change_status(started.run_id).phase == (
        ChangeRunPhaseV1.READY_TO_ACTIVATE
    )

    generation_root = settings.paths.change_control_generation_root
    tampered_index: Path | None = None
    original_index = b""

    def tamper_index(boundary: str, *, expected: str) -> None:
        nonlocal tampered_index, original_index
        if boundary != expected:
            return
        matches = tuple(generation_root.glob("generations/*/index/mastervault.sqlite3"))
        assert len(matches) == 1
        tampered_index = matches[0]
        original_index = tampered_index.read_bytes()
        tampered_index.chmod(0o600)
        with tampered_index.open("ab") as stream:
            stream.write(b"tamper-after-application-boundary")

    with pytest.raises(ChangeControlApplicationIntegrityError):
        downstream.activate_change(
            settings=settings,
            request=activation_request,
            failure_hook=lambda boundary: tamper_index(
                boundary, expected="activation-receipt-owned"
            ),
        )
    assert tampered_index is not None
    tampered_index.write_bytes(original_index)
    tampered_index.chmod(0o400)
    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        activation_link_count = store.conn.execute(
            "SELECT count(*) FROM change_control_operator_run_links "
            "WHERE run_id=? AND link_kind='activation-operation'",
            (started.run_id,),
        ).fetchone()[0]
    finally:
        store.close()
    assert activation_link_count == 0

    with pytest.raises(ChangeControlApplicationIntegrityError):
        downstream.activate_change(
            settings=settings,
            request=activation_request,
            failure_hook=lambda boundary: tamper_index(boundary, expected="activation-linked"),
        )
    assert tampered_index is not None
    tampered_index.write_bytes(original_index)
    tampered_index.chmod(0o400)

    activated = downstream.activate_change(settings=settings, request=activation_request)
    replay = downstream.activate_change(settings=settings, request=activation_request)
    assert activated == replay
    assert activated.phase == ChangeRunPhaseV1.ACTIVATED
    assert activated.activation_receipt_id is not None
    assert application.verify_change(started.run_id).status.phase == ChangeRunPhaseV1.ACTIVATED
    with application.resolve_query_generation(QueryGenerationSelector.ACTIVE) as resolved_active:
        assert resolved_active.metadata.generation_kind == QueryGenerationKind.MANAGED
        assert resolved_active.metadata.generation_number == 1
        assert resolved_active.metadata.is_active is True
        resolved_active.verify()
    assert application.get_status(started.run_id).record.command.run_id == started.run_id
    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        row = store.conn.execute(
            "SELECT run_id,baseline_receipt_id FROM "
            "change_control_activation_baseline_bindings WHERE activation_id=("
            "SELECT activation_id FROM change_control_generation_activation_receipts "
            "WHERE receipt_id=?)",
            (activated.activation_receipt_id,),
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    assert str(row["run_id"]) == started.run_id
    baseline = application.get_change_status(started.run_id).baseline
    assert baseline is not None
    assert baseline.baseline_id == activated.baseline_id
    assert str(row["baseline_receipt_id"]) == baseline.receipt_id


def test_real_operator_mixed_review_publishes_only_the_approved_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mastervault.change_control.application_generic_extraction as extraction_module
    import mastervault.change_control.application_provider_bridge as bridge_module
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.change_application_contracts import (
        ActivateChangeRequestV1,
        ChangeExecutionModeV1,
        ChangeReviewSubjectKindV1,
        ChangeRunPhaseV1,
        ManagedReviewChoiceV1,
        ManagedReviewDecisionDocumentV1,
        ManagedReviewDecisionItemV1,
        StartChangeRequestV1,
    )
    from mastervault.change_control.managed_generation import (
        ManagedGenerationActivationReceipt,
    )
    from mastervault.change_control.managed_store import SqliteManagedChangeControlStore
    from mastervault.config import Settings
    from mastervault.models import Domain

    workspace, manifest = _operator_workspace(tmp_path, include_training_guide=True)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    application = ChangeControlApplication(settings)
    application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    llm = _AdoptionOnlyLifecycleLLM(mixed_impact=True)
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    started = application.start_change(
        StartChangeRequestV1(
            operation_id="downstream-test:mixed-start",
            source=_incoming_source(tmp_path),
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=_regression_suite(tmp_path),
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    temporal = application.get_change_review(started.run_id)
    awaiting = downstream.record_change_review(
        settings=settings,
        document=TemporalReviewDecisionDocumentV1.create(
            run_id=temporal.run_id,
            request_id=temporal.request_id,
            request_sha256=temporal.request_sha256,
            operation_id="downstream-test:mixed-temporal",
            reviewer_id="reviewer.operator",
            rationale="Accept the exact governing source.",
            decisions=tuple(
                TemporalReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=TemporalReviewChoiceV1.ACCEPT,
                )
                for item in temporal.subjects
            ),
        ),
    )
    assert awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    managed = application.get_change_review(started.run_id)
    assert {item.subject_kind for item in managed.subjects} == {
        ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN,
        ChangeReviewSubjectKindV1.NO_CHANGE_IMPACT_CARD,
    }
    ready = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=managed.run_id,
            request_id=managed.request_id,
            request_sha256=managed.request_sha256,
            operation_id="downstream-test:mixed-managed",
            reviewer_id="reviewer.operator",
            rationale="Approve the revision and confirm the independent no-change result.",
            decisions=tuple(
                ManagedReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=(
                        ManagedReviewChoiceV1.APPROVE
                        if item.subject_kind == ChangeReviewSubjectKindV1.MANAGED_REVISION_PLAN
                        else ManagedReviewChoiceV1.CONFIRM_NO_CHANGE
                    ),
                )
                for item in managed.subjects
            ),
        ),
    )
    assert ready.phase == ChangeRunPhaseV1.READY_TO_ACTIVATE
    activated = downstream.activate_change(
        settings=settings,
        request=ActivateChangeRequestV1(
            run_id=started.run_id,
            operation_id="downstream-test:mixed-activate",
        ),
    )
    assert activated.phase == ChangeRunPhaseV1.ACTIVATED
    assert activated.activation_receipt_id is not None
    store = SqliteManagedChangeControlStore(
        settings.paths.change_control_db_path, secure_open=True, read_only=True
    )
    try:
        row = store.conn.execute(
            "SELECT payload_json FROM change_control_generation_activation_receipts "
            "WHERE receipt_id=?",
            (activated.activation_receipt_id,),
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    receipt = ManagedGenerationActivationReceipt.model_validate_json(
        str(row["payload_json"]), strict=True
    )
    assert receipt.publication_count == 2
    published_markdown = tuple(
        path.read_text(encoding="utf-8")
        for path in settings.paths.change_control_generation_root.rglob("*.md")
    )
    assert any("Service agents ask customers" in text for text in published_markdown)


def test_full_offline_replay_runs_downstream_with_zero_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mastervault.change_control.application_generic_extraction as extraction_module
    import mastervault.change_control.application_provider_bridge as bridge_module
    import mastervault.change_control.application_start_lifecycle as start_module
    import mastervault.change_control.store as store_module
    from mastervault.change_control.application import ChangeControlApplication
    from mastervault.change_control.application_extraction_calls import (
        ApplicationExtractionCallRepository,
    )
    from mastervault.change_control.application_lifecycle_evidence import (
        FilesystemLifecycleEvidenceIndex,
        LifecycleEvidenceStageV1,
    )
    from mastervault.change_control.application_replay import (
        ChangeReplayBundleV1,
        ChangeReplayStageEvidenceV1,
        ChangeReplayStageV1,
        capture_completed_live_replay_bundle,
    )
    from mastervault.change_control.application_runtime_identity import (
        application_configuration_sha256,
    )
    from mastervault.change_control.application_stage_evidence import (
        ApplicationStageEvidenceRepository,
    )
    from mastervault.change_control.application_start_command import (
        ApplicationStartCommandRepository,
    )
    from mastervault.change_control.change_application_contracts import (
        ChangeExecutionModeV1,
        ChangeRunPhaseV1,
        ManagedReviewChoiceV1,
        ManagedReviewDecisionDocumentV1,
        ManagedReviewDecisionItemV1,
        StartChangeRequestV1,
    )
    from mastervault.change_control.generic_incoming import (
        GenericExtractionModeV2,
        admit_generic_incoming_markdown_v2,
        ground_generic_extraction_v2,
    )
    from mastervault.change_control.generic_incoming_repository import (
        FilesystemGenericIncomingRepositoryV2,
    )
    from mastervault.change_control.inference_repository import (
        FilesystemInferenceEvidenceRepository,
    )
    from mastervault.change_control.recorded_inference import RecordedInferenceTask
    from mastervault.change_control.regression_baseline import (
        GenerationZeroBaselineRepository,
    )
    from mastervault.change_control.regression_suite import load_regression_suite
    from mastervault.config import Settings
    from mastervault.models import Domain

    workspace, manifest = _operator_workspace(tmp_path)
    settings = Settings.model_validate(
        {
            "storage": {"backend": "sqlite"},
            "embedding": {"provider": "mock"},
            "llm": {
                "provider": "mock",
                "model_small": "mock-small",
                "model_medium": "mock-medium",
                "model_large": "mock-large",
            },
            "paths": {"workspace": workspace},
            "query_generation": {
                "bootstrap_manifest": manifest,
                "canonical_repository_root": workspace / "vault",
            },
        }
    )
    application = ChangeControlApplication(settings)
    bootstrap = application.bootstrap(manifest, BOOTSTRAP_OPERATION)
    bootstrap_run_id = bootstrap.operator_run.record.command.run_id
    state_snapshot = settings.paths.change_control_db_path.read_bytes()
    embedding = MockEmbedding()
    embedding_calls: list[tuple[str, ...]] = []
    original_embed = embedding.embed

    def counted_embed(texts: list[str]) -> list[list[float]]:
        embedding_calls.append(tuple(texts))
        return original_embed(texts)

    monkeypatch.setattr(embedding, "embed", counted_embed)
    llm = _AdoptionOnlyLifecycleLLM()
    monkeypatch.setattr(extraction_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(bridge_module, "get_llm", lambda _settings: llm)
    monkeypatch.setattr(store_module, "_now", lambda: "2026-08-20T12:00:00+00:00")
    monkeypatch.setattr(start_module, "get_embedding_provider", lambda _settings: embedding)
    source = _incoming_source(tmp_path)
    suite = _regression_suite(tmp_path)
    live = application.start_change(
        StartChangeRequestV1(
            operation_id="downstream-test:replay-source-live",
            requested_run_id=bootstrap_run_id,
            source=source,
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=suite,
            mode=ChangeExecutionModeV1.LIVE,
        )
    )
    temporal = application.get_change_review(live.run_id)
    live_temporal_document = TemporalReviewDecisionDocumentV1.create(
        run_id=temporal.run_id,
        request_id=temporal.request_id,
        request_sha256=temporal.request_sha256,
        operation_id="downstream-test:replay-source-temporal",
        reviewer_id="reviewer.operator",
        rationale="Accept the exact source before replay capture.",
        decisions=tuple(
            TemporalReviewDecisionItemV1(
                subject_id=item.subject_id,
                subject_sha256=item.subject_sha256,
                subject_kind=cast(Any, item.subject_kind),
                choice=TemporalReviewChoiceV1.ACCEPT,
            )
            for item in temporal.subjects
        ),
    )
    live_awaiting = downstream.record_change_review(
        settings=settings,
        document=live_temporal_document,
    )
    assert live_awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    evidence_root = settings.paths.change_control_evidence_root
    live_command = ApplicationStartCommandRepository(
        evidence_root, create=False, read_only=True
    ).reopen_run(live.run_id)
    incoming_owner = (
        FilesystemLifecycleEvidenceIndex(evidence_root, create=False, read_only=True)
        .reopen(live.run_id, LifecycleEvidenceStageV1.INCOMING)
        .owners[0]
    )
    generic_repository = FilesystemGenericIncomingRepositoryV2(evidence_root)
    live_incoming = generic_repository.resolve_verified_evidence(
        generic_repository.reopen(incoming_owner.owner_id)
    )
    extraction = ApplicationExtractionCallRepository(
        evidence_root, create=False, read_only=True
    ).reopen_completed(
        start_command_id=live_command.command_id,
        extraction_request_sha256=live_incoming.inference.request_sha256,
    )
    replay_admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    assert replay_admission.source_sha256 == live_incoming.admission.source_sha256
    assert replay_admission.metadata == live_incoming.admission.metadata
    replayed_grounding = ground_generic_extraction_v2(
        replay_admission,
        extraction.provider_contract,
        mode=GenericExtractionModeV2.REPLAY,
        replay_of=extraction.grounded_extraction,
    )
    replayed_incoming = generic_repository.resolve_verified_evidence(
        generic_repository.persist(replay_admission, replayed_grounding)
    )
    replay_run_id = bootstrap_run_id
    captured = capture_completed_live_replay_bundle(
        evidence_root=evidence_root,
        source_run_id=live.run_id,
        current_run_id=replay_run_id,
        current_incoming_bundle_id=replayed_incoming.bundle.bundle_id,
        current_incoming_bundle_sha256=replayed_incoming.bundle.bundle_sha256,
        configuration_sha256=application_configuration_sha256(settings),
    )
    baseline_repository = GenerationZeroBaselineRepository(evidence_root)
    live_baseline = baseline_repository.open(live.run_id)
    source_baseline_run_id = f"operatorrun:{'8' * 64}"
    prepared_source_baseline = baseline_repository.prepare_replay(
        source_reference=live_baseline.replay_ref,
        current_authority=live_baseline.authority.model_copy(
            update={
                "run_id": source_baseline_run_id,
                "incoming_admission_receipt_id": "incomingreceipt:offline-replay-source",
                "incoming_admission_receipt_sha256": "8" * 64,
            }
        ),
        current_suite=load_regression_suite(suite),
        expected_runtime=live_baseline.runtime,
    )
    copied_source_baseline = baseline_repository.publish(
        prepared_source_baseline.prepared,
        captured_at=prepared_source_baseline.captured_at,
    ).receipt
    replay_bundle = ChangeReplayBundleV1.create(
        run_id=captured.bundle.run_id,
        incoming_bundle_id=captured.bundle.incoming_bundle_id,
        incoming_bundle_sha256=captured.bundle.incoming_bundle_sha256,
        configuration_sha256=captured.bundle.configuration_sha256,
        stages=tuple(
            ChangeReplayStageEvidenceV1(
                stage=item.stage,
                artifacts=(copied_source_baseline.replay_ref,),
            )
            if item.stage == ChangeReplayStageV1.BASELINE
            else item
            for item in captured.bundle.stages
        ),
    )
    replay_path = tmp_path / "full-replay.json"
    replay_path.write_bytes(canonical_json_bytes(replay_bundle.model_dump(mode="json")))
    provider_calls = len(llm.calls)
    live_embedding_call_count = len(embedding_calls)
    settings.paths.change_control_db_path.write_bytes(state_snapshot)
    settings.paths.change_control_db_path.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{settings.paths.change_control_db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    shutil.rmtree(evidence_root / "application" / "start-commands")
    shutil.rmtree(
        evidence_root
        / "regression-baselines"
        / "runs"
        / hashlib.sha256(replay_run_id.encode("utf-8")).hexdigest()
    )
    run_evidence_name = hashlib.sha256(replay_run_id.encode("utf-8")).hexdigest()
    shutil.rmtree(evidence_root / "application" / "stage-evidence" / run_evidence_name)
    no_work_root = evidence_root / "application" / "no-work-planning" / run_evidence_name
    if no_work_root.exists():
        shutil.rmtree(no_work_root)
    (evidence_root / "staging" / "managed-review" / replay_run_id / "COMPLETE.json").unlink()
    lifecycle_root = evidence_root / "application" / "lifecycle-index-v1"
    for index_path in lifecycle_root.glob("*.json"):
        index_path.unlink()

    def offline_provider(_settings: Settings) -> Any:
        raise AssertionError("offline replay must not construct an LLM provider")

    monkeypatch.setattr(extraction_module, "get_llm", offline_provider)
    monkeypatch.setattr(bridge_module, "get_llm", offline_provider)
    replay_application = ChangeControlApplication(settings)
    replay_started = replay_application.start_change(
        StartChangeRequestV1(
            operation_id="downstream-test:replay-source-live",
            requested_run_id=replay_run_id,
            source=source,
            domain=Domain.CUSTOMER_SUPPORT,
            regression_suite=suite,
            mode=ChangeExecutionModeV1.REPLAY,
            replay_bundle=replay_path,
        )
    )
    assert len(llm.calls) == provider_calls
    assert len(embedding_calls) == live_embedding_call_count
    replay_temporal = replay_application.get_change_review(replay_started.run_id)
    assert replay_temporal.request_id == temporal.request_id
    assert replay_temporal.request_sha256 == temporal.request_sha256
    assert replay_temporal.subjects == temporal.subjects
    replay_awaiting = downstream.record_change_review(
        settings=settings,
        document=live_temporal_document,
    )
    assert replay_awaiting.phase == ChangeRunPhaseV1.AWAITING_MANAGED_REVIEW
    assert len(llm.calls) == provider_calls
    assert len(embedding_calls) == live_embedding_call_count
    stage_repository = ApplicationStageEvidenceRepository(
        evidence_root, create=False, read_only=True
    )
    replay_inference = FilesystemInferenceEvidenceRepository(
        evidence_root, create=False, read_only=True
    )
    replayed_stage_outcomes: list[Any] = []
    for task, stage_evidence in (
        (RecordedInferenceTask.IMPACT, stage_repository.reopen_impact(replay_run_id)),
        (
            RecordedInferenceTask.REVISION_PLANNING,
            stage_repository.reopen_planning(replay_run_id),
        ),
    ):
        outcomes = replay_inference.resolve_batch(
            batch_id=stage_evidence.binding.batch_id,
            batch_sha256=stage_evidence.binding.batch_sha256,
        )
        assert outcomes
        assert all(item.execution.task == task for item in outcomes)
        replayed_stage_outcomes.extend(outcomes)
    replay_source_receipts = {
        item.artifact_id
        for stage in replay_bundle.stages
        if stage.stage in {ChangeReplayStageV1.IMPACT, ChangeReplayStageV1.PLANNING}
        for item in (ref.recorded_inference_receipt() for ref in stage.artifacts)
    }
    for outcome in replayed_stage_outcomes:
        execution = outcome.execution
        attestation = execution.replay_rebase_attestation
        assert attestation is not None
        assert attestation.source_semantic_sha256 == attestation.current_semantic_sha256
        assert attestation.source_receipt_artifact.artifact_id in replay_source_receipts
        source_outcome = replay_inference.resolve_replay_evidence(
            receipt_artifact=attestation.source_receipt_artifact
        )
        assert attestation.source_receipt_artifact == source_outcome.execution.receipt_artifact
        assert (
            attestation.source_raw_output_artifact == source_outcome.execution.raw_output_artifact
        )
        assert (
            attestation.source_validated_output_artifact
            == source_outcome.execution.validated_output_artifact
        )
        assert attestation.current_validated_output_artifact == (
            execution.validated_output_artifact
        )
        mappings = {item.kind: item for item in attestation.mappings}
        assert mappings["workload"].current_id == execution.input_envelope.workload_id
        assert mappings["workload"].current_sha256 == (execution.input_envelope.workload_sha256)
        assert mappings["input-envelope"].current_id == (execution.input_envelope.envelope_id)
        assert mappings["input-envelope"].current_sha256 == (
            execution.input_envelope.envelope_sha256
        )
        current_output = outcome.impact_output or outcome.revision_planning_output
        assert current_output is not None
        assert mappings["validated-output"].current_id == current_output.output_shard_id
        assert mappings["validated-output"].current_sha256 == (current_output.output_shard_sha256)
    replay_managed = replay_application.get_change_review(replay_started.run_id)
    replay_ready = downstream.record_change_review(
        settings=settings,
        document=ManagedReviewDecisionDocumentV1.create(
            run_id=replay_managed.run_id,
            request_id=replay_managed.request_id,
            request_sha256=replay_managed.request_sha256,
            operation_id="downstream-test:offline-replay-managed",
            reviewer_id="reviewer.operator",
            rationale="Confirm every exact replayed no-change target.",
            decisions=tuple(
                ManagedReviewDecisionItemV1(
                    subject_id=item.subject_id,
                    subject_sha256=item.subject_sha256,
                    subject_kind=cast(Any, item.subject_kind),
                    choice=ManagedReviewChoiceV1.CONFIRM_NO_CHANGE,
                )
                for item in replay_managed.subjects
            ),
        ),
    )
    assert replay_ready.phase == ChangeRunPhaseV1.READY_TO_ACTIVATE
    assert len(llm.calls) == provider_calls
    assert len(embedding_calls) == live_embedding_call_count
