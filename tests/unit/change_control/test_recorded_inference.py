from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.change_control.classification import (
    ClassificationInferenceShard,
    ClassificationWorkload,
    select_classification_workload,
)
from mastervault.change_control.dependency_analysis import (
    CanonicalSourceNoteSnapshot,
    DependencyCandidate,
    DependencyCandidateRef,
    DependencyClassification,
    DependencyDisposition,
    DependencyInferenceShard,
    DependencyWorkload,
    DependencyWorkloadIndex,
    GoverningSupersessionRef,
)
from mastervault.change_control.discovery import generate_relationship_candidates
from mastervault.change_control.managed_review import (
    InferenceExecutionMode,
    InferenceUsage,
    ManagedArtifactKind,
    ManagedArtifactRef,
    ManagedInferenceContractBinding,
)
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    RelationGraph,
    TemporalConstraintSet,
    TemporalResolutionContext,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.recorded_inference import (
    MAX_PROVIDER_OUTPUT_BYTES_V1,
    MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1,
    InferenceArtifactPayload,
    InferenceAttemptEvidence,
    InferenceExecutionFailed,
    InferenceInputEnvelope,
    InferenceProviderRequest,
    ProviderCallResult,
    RecordedInferenceOutcome,
    _rehydrate_dependency_output,
    run_classification_inference,
    run_dependency_inference,
)
from mastervault.change_control.store import ChangeControlSnapshot

SHA_A = "a" * 64
ALGORITHM = b'{"algorithm":"recorded-v1"}'
PROMPT = b"Return one strict decision per supplied identifier."
SCHEMA = b'{"type":"object","additionalProperties":false}'
PACKAGE_PATH = (
    Path(__file__).resolve().parents[3] / "src/mastervault/change_control/recorded_inference.py"
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_sha(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _document(key: str, *, family: str, start: date) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=key,
        document_family=family,
        version_label=key,
        source_path=f"runtime/raw/{key}.md",
        source_sha256=SHA_A,
        declared_effective_from=start,
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )


def _claim(
    document: DocumentVersionMetadata,
    local_id: str,
    *,
    statement: str,
    source_note_path: str | None = None,
    source_note_sha256: str = SHA_A,
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=source_note_path or f"runtime/sources/{document.document_id}.md",
            source_note_sha256=source_note_sha256,
            source_claim_id=local_id,
        ),
        statement=statement,
        declared_effective_from=document.declared_effective_from,
        scopes=("policy.returns",),
    )


def _aggregate(
    documents: tuple[DocumentVersionMetadata, ...],
    claims: tuple[VersionedClaimRevision, ...],
) -> ChangeControlAggregate:
    return ChangeControlAggregate.create(
        aggregate_id="recorded-inference-fixture",
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )


def _snapshot(aggregate: ChangeControlAggregate) -> ChangeControlSnapshot:
    return ChangeControlSnapshot(
        aggregate=aggregate,
        revision=2,
        aggregate_sha256=aggregate_sha256(aggregate),
    )


def _classification_fixture() -> tuple[
    ClassificationWorkload,
    ClassificationInferenceShard,
    str,
    str,
]:
    newer_doc = _document("returns-v2", family="policy.returns", start=date(2026, 1, 1))
    older_doc = _document("returns-v1", family="policy.returns", start=date(2024, 1, 1))
    newer = _claim(
        newer_doc,
        "returns-new-01",
        statement="Customers may return an eligible item within forty five days.",
    )
    older = _claim(
        older_doc,
        "returns-old-01",
        statement="Customers may return an eligible item within thirty days.",
    )
    snapshot = _snapshot(_aggregate((newer_doc, older_doc), (newer, older)))
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(newer.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    workload = select_classification_workload(snapshot, candidates=candidates)
    return workload, workload.inference_shards[0], newer.claim_revision_id, older.claim_revision_id


def _dependency_fixture() -> tuple[DependencyWorkload, DependencyInferenceShard, str, int, int]:
    changed_doc = _document("returns-v2", family="policy.returns", start=date(2026, 1, 1))
    older_doc = _document("returns-v1", family="policy.returns", start=date(2024, 1, 1))
    downstream_doc = _document("faq-v1", family="support.faq", start=date(2025, 1, 1))
    note_text = "---\ntitle: FAQ\n---\nBody says customers have thirty days to return an item.\n"
    note = CanonicalSourceNoteSnapshot.create(
        document=downstream_doc,
        source_note_path="runtime/sources/faq-v1.md",
        source_note_utf8=note_text,
        body_start_char=note_text.index("Body"),
    )
    changed = _claim(
        changed_doc,
        "returns-new-01",
        statement="Customers may return an eligible item within forty five days.",
    )
    older = _claim(
        older_doc,
        "returns-old-01",
        statement="Customers may return an eligible item within thirty days.",
    )
    downstream = _claim(
        downstream_doc,
        "faq-window-01",
        statement="Customers have thirty days to return an eligible item.",
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
    )
    aggregate = _aggregate((changed_doc, older_doc, downstream_doc), (changed, older, downstream))
    governing = GoverningSupersessionRef(
        pair_id="pair:" + "1" * 64,
        candidate_sha256="2" * 64,
        classification_id="pairclass:" + "3" * 64,
        classification_sha256="4" * 64,
        relation_id="rel:" + "5" * 64,
        changed_claim_revision_id=changed.claim_revision_id,
        upstream_claim_revision_id=older.claim_revision_id,
    )
    candidate = DependencyCandidate.create(
        governing=governing,
        changed_claim_revision=changed,
        upstream_claim_revision=older,
        downstream_document_version_id=downstream_doc.document_version_id,
        selected_neighbour_refs=(),
    )
    temporal = TemporalResolutionContext.from_aggregate(
        aggregate,
        as_of=date(2026, 2, 1),
    ).resolve_document(downstream_doc)
    shard = DependencyInferenceShard.create(
        downstream_note=note,
        downstream_claim_revisions=(downstream,),
        temporal_resolution=temporal,
        candidates=(candidate,),
    )
    candidate_ref = DependencyCandidateRef(
        document_version_id=downstream_doc.document_version_id,
        changed_claim_revision_id=changed.claim_revision_id,
        upstream_claim_revision_id=older.claim_revision_id,
        candidate_id=candidate.candidate_id,
        candidate_sha256=candidate.candidate_sha256,
        input_shard_id=shard.shard_id,
        input_shard_sha256=shard.shard_sha256,
    )
    index_values = {
        "aggregate_id": aggregate.aggregate_id,
        "snapshot_revision": 2,
        "aggregate_sha256": aggregate_sha256(aggregate),
        "inventory_sha256": "6" * 64,
        "source_candidate_set_sha256": "7" * 64,
        "source_classification_result_id": "classresult:" + "8" * 64,
        "source_classification_result_sha256": "8" * 64,
        "governing_supersessions": (governing,),
        "candidate_refs": (candidate_ref,),
        "exclusion_refs": (),
    }
    payload = {
        "namespace": "mastervault.dependency-workload-index.v1",
        "schema_version": 1,
        **{
            key: (
                [item.model_dump(mode="json") for item in value]
                if isinstance(value, tuple)
                else value
            )
            for key, value in index_values.items()
        },
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    index = DependencyWorkloadIndex(
        **index_values,
        workload_id=f"depwork:{digest}",
        workload_sha256=digest,
    )
    start = note_text.index("customers")
    end = note_text.index(".", start) + 1
    return (
        DependencyWorkload(index=index, input_shards=(shard,), exclusions=()),
        shard,
        candidate.candidate_id,
        start,
        end,
    )


def _two_candidate_dependency_fixture() -> tuple[DependencyWorkload, DependencyInferenceShard]:
    workload, shard, *_ = _dependency_fixture()
    changed_doc = _document("warranty-v2", family="policy.warranty", start=date(2026, 1, 2))
    older_doc = _document("warranty-v1", family="policy.warranty", start=date(2024, 1, 2))
    changed = _claim(
        changed_doc,
        "warranty-new-01",
        statement="Premium warranties remain valid for three years.",
    )
    older = _claim(
        older_doc,
        "warranty-old-01",
        statement="Premium warranties remain valid for two years.",
    )
    governing = GoverningSupersessionRef(
        pair_id="pair:" + "9" * 64,
        candidate_sha256="a" * 64,
        classification_id="pairclass:" + "b" * 64,
        classification_sha256="c" * 64,
        relation_id="rel:" + "d" * 64,
        changed_claim_revision_id=changed.claim_revision_id,
        upstream_claim_revision_id=older.claim_revision_id,
    )
    second = DependencyCandidate.create(
        governing=governing,
        changed_claim_revision=changed,
        upstream_claim_revision=older,
        downstream_document_version_id=shard.downstream_note.document.document_version_id,
        selected_neighbour_refs=(),
    )
    two_shard = DependencyInferenceShard.create(
        downstream_note=shard.downstream_note,
        downstream_claim_revisions=shard.downstream_claim_revisions,
        temporal_resolution=shard.temporal_resolution,
        candidates=(*shard.candidates, second),
    )
    governing_refs = tuple(
        sorted(
            (*workload.index.governing_supersessions, governing),
            key=lambda item: (
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            ),
        )
    )
    candidate_refs = tuple(
        sorted(
            (
                DependencyCandidateRef(
                    document_version_id=(two_shard.downstream_note.document.document_version_id),
                    changed_claim_revision_id=candidate.governing.changed_claim_revision_id,
                    upstream_claim_revision_id=candidate.governing.upstream_claim_revision_id,
                    candidate_id=candidate.candidate_id,
                    candidate_sha256=candidate.candidate_sha256,
                    input_shard_id=two_shard.shard_id,
                    input_shard_sha256=two_shard.shard_sha256,
                )
                for candidate in two_shard.candidates
            ),
            key=lambda item: (
                item.document_version_id,
                item.changed_claim_revision_id,
                item.upstream_claim_revision_id,
            ),
        )
    )
    index_values = {
        "aggregate_id": workload.index.aggregate_id,
        "snapshot_revision": workload.index.snapshot_revision,
        "aggregate_sha256": workload.index.aggregate_sha256,
        "inventory_sha256": workload.index.inventory_sha256,
        "source_candidate_set_sha256": workload.index.source_candidate_set_sha256,
        "source_classification_result_id": workload.index.source_classification_result_id,
        "source_classification_result_sha256": (workload.index.source_classification_result_sha256),
        "governing_supersessions": governing_refs,
        "candidate_refs": candidate_refs,
        "exclusion_refs": (),
    }
    payload = {
        "namespace": "mastervault.dependency-workload-index.v1",
        "schema_version": 1,
        **{
            key: (
                [item.model_dump(mode="json") for item in value]
                if isinstance(value, tuple)
                else value
            )
            for key, value in index_values.items()
        },
    }
    digest = _payload_sha(payload)
    index = DependencyWorkloadIndex(
        **index_values,
        workload_id=f"depwork:{digest}",
        workload_sha256=digest,
    )
    return DependencyWorkload(index=index, input_shards=(two_shard,), exclusions=()), two_shard


def _contract(mode: InferenceExecutionMode) -> ManagedInferenceContractBinding:
    return ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=_sha(ALGORITHM),
        contract_id="recorded-change-control-v1",
        contract_version=1,
        mode=mode,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=_sha(PROMPT),
        response_schema_sha256=_sha(SCHEMA),
    )


def _usage() -> InferenceUsage:
    return InferenceUsage(
        input_tokens=100,
        output_tokens=25,
        cached_input_tokens=0,
        cost_usd_micros=50,
        latency_ms=20,
    )


@dataclass
class _Provider:
    outputs: list[str]
    provider: str = "fixture-provider"
    model: str = "fixture-model"
    request_ids: list[str] | None = None
    calls: int = 0
    corrections: tuple[str | None, ...] = ()
    requests: tuple[InferenceProviderRequest, ...] = ()

    def complete(
        self,
        *,
        request: bytes,
    ) -> ProviderCallResult:
        parsed = InferenceProviderRequest.model_validate_json(request)
        self.calls += 1
        self.requests = (*self.requests, parsed)
        self.corrections = (
            *self.corrections,
            parsed.correction.validation_error if parsed.correction is not None else None,
        )
        request_id = (
            self.request_ids[self.calls - 1]
            if self.request_ids is not None
            else f"fixture:req-{self.calls}"
        )
        return ProviderCallResult(
            provider=self.provider,
            model=self.model,
            provider_request_id=request_id,
            raw_output_utf8=self.outputs[self.calls - 1],
            usage=_usage(),
        )


@dataclass(frozen=True)
class _Resolver:
    outcome: RecordedInferenceOutcome
    calls: list[ManagedArtifactRef]

    def resolve_replay_evidence(
        self,
        *,
        receipt_artifact: ManagedArtifactRef,
    ) -> RecordedInferenceOutcome:
        self.calls.append(receipt_artifact)
        return self.outcome


def _classification_wire(pair_id: str, newer_id: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "task": "classification",
            "decisions": [
                {
                    "pair_id": pair_id,
                    "disposition": "SUPERSEDES",
                    "newer_revision_id": newer_id,
                    "rationale": "The later policy replaces the earlier return window.",
                    "confidence": 0.95,
                }
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _run_live_classification() -> tuple[RecordedInferenceOutcome, _Provider]:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider([_classification_wire(shard.pairs[0].candidate.pair_id, newer)])
    outcome = run_classification_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )
    return outcome, provider


def test_live_classification_records_truthful_one_call_and_sharded_output() -> None:
    outcome, provider = _run_live_classification()

    assert provider.calls == 1
    assert outcome.execution.receipt.mode == InferenceExecutionMode.LIVE
    assert outcome.execution.receipt.provider_request_id == "fixture:req-1"
    assert outcome.execution.attempts[0].accepted
    assert outcome.classification_output is not None
    assert len(outcome.classification_output.canonical_bytes()) <= 256 * 1024
    request = provider.requests[0]
    assert request.prompt_utf8 == PROMPT.decode("utf-8")
    assert "forty five days" in request.input_shard_utf8
    assert "thirty days" in request.input_shard_utf8
    assert request.response_schema_utf8 == SCHEMA.decode("utf-8")
    assert len(request.canonical_bytes()) <= MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1


def test_live_dependency_constructs_exact_span_and_output_shard() -> None:
    workload, shard, candidate_id, start, end = _dependency_fixture()
    wire = json.dumps(
        {
            "schema_version": 1,
            "task": "dependency",
            "decisions": [
                {
                    "candidate_id": candidate_id,
                    "disposition": "DEPENDS_ON",
                    "dependency_kind": "summarizes",
                    "selected_downstream_claim_revision_ids": [],
                    "spans": [{"start_char": start, "end_char": end}],
                    "rationale": "The FAQ repeats the older policy window.",
                    "confidence": 0.9,
                }
            ],
        }
    )
    outcome = run_dependency_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=_Provider([wire]),
    )

    assert outcome.dependency_output is not None
    result = outcome.dependency_output.classifications[0]
    assert result.downstream_spans[0].quote == shard.downstream_note.source_note_utf8[start:end]
    assert result.downstream_spans[0].source_note_sha256 == shard.downstream_note.source_note_sha256


def test_dependency_replay_reconstructs_exact_canonical_output_without_provider() -> None:
    workload, shard, candidate_id, start, end = _dependency_fixture()
    wire = json.dumps(
        {
            "schema_version": 1,
            "task": "dependency",
            "decisions": [
                {
                    "candidate_id": candidate_id,
                    "disposition": "DEPENDS_ON",
                    "dependency_kind": "summarizes",
                    "selected_downstream_claim_revision_ids": [],
                    "spans": [{"start_char": start, "end_char": end}],
                    "rationale": "The FAQ repeats the older policy window.",
                    "confidence": 0.9,
                }
            ],
        }
    )
    live = run_dependency_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=_Provider([wire]),
    )
    resolver = _Resolver(live, [])

    replay = run_dependency_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=resolver,
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )

    assert resolver.calls == [live.execution.receipt_artifact]
    assert replay.dependency_output == live.dependency_output
    assert replay.execution.receipt.mode == InferenceExecutionMode.REPLAY


def test_one_correction_retains_rejected_attempt_evidence() -> None:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider(
        ["not-json", _classification_wire(shard.pairs[0].candidate.pair_id, newer)]
    )

    outcome = run_classification_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert provider.calls == 2
    assert provider.corrections[0] is None and provider.corrections[1]
    assert provider.requests[1].correction is not None
    assert provider.requests[1].correction.previous_raw_output_utf8 == "not-json"
    assert [item.accepted for item in outcome.execution.attempts] == [False, True]
    assert outcome.execution.attempts[0].validation_error
    assert any(item.content_utf8 == "not-json" for item in outcome.artifacts)


def test_max_control_character_correction_request_stays_bounded() -> None:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider(
        [
            "\x00" * MAX_PROVIDER_OUTPUT_BYTES_V1,
            _classification_wire(shard.pairs[0].candidate.pair_id, newer),
        ]
    )

    outcome = run_classification_inference(
        contract=_contract(InferenceExecutionMode.LIVE),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        provider=provider,
    )

    assert outcome.execution.attempts[-1].accepted
    assert provider.calls == 2
    assert len(provider.requests[1].canonical_bytes()) <= (MAX_PROVIDER_REQUEST_CANONICAL_BYTES_V1)
    assert provider.requests[1].correction is not None
    assert len(provider.requests[1].correction.previous_raw_output_utf8) == (
        MAX_PROVIDER_OUTPUT_BYTES_V1
    )


def test_oversize_initial_provider_request_fails_before_provider_call() -> None:
    workload, shard, newer, _older = _classification_fixture()
    prompt = b"\x00" * (256 * 1024)
    schema = b"\x01" * (256 * 1024)
    contract = ManagedInferenceContractBinding.create(
        algorithm_manifest_sha256=_sha(ALGORITHM),
        contract_id="recorded-change-control-v1",
        contract_version=1,
        mode=InferenceExecutionMode.LIVE,
        provider="fixture-provider",
        model="fixture-model",
        prompt_sha256=_sha(prompt),
        response_schema_sha256=_sha(schema),
    )
    provider = _Provider([_classification_wire(shard.pairs[0].candidate.pair_id, newer)])

    with pytest.raises(InferenceExecutionFailed, match="3 MiB canonical") as error:
        run_classification_inference(
            contract=contract,
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=prompt,
            response_schema_bytes=schema,
            provider=provider,
        )

    assert provider.calls == 0
    assert error.value.attempts == ()


def test_second_failure_emits_no_successful_execution() -> None:
    workload, shard, *_ = _classification_fixture()
    provider = _Provider(["bad-one", "bad-two"])

    with pytest.raises(InferenceExecutionFailed) as error:
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.LIVE),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=provider,
        )

    assert provider.calls == 2
    assert len(error.value.attempts) == 2
    assert not any(item.accepted for item in error.value.attempts)


def test_provider_evidence_mismatch_is_rejected_not_synthesized() -> None:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider(
        [_classification_wire(shard.pairs[0].candidate.pair_id, newer)] * 2,
        provider="substituted-provider",
    )
    with pytest.raises(InferenceExecutionFailed) as error:
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.LIVE),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=provider,
        )
    assert provider.calls == 1
    assert len(error.value.attempts) == 1
    assert "provider/model evidence" in (error.value.attempts[0].validation_error or "")


def test_live_retry_rejects_reused_provider_request_id() -> None:
    workload, shard, newer, _older = _classification_fixture()
    provider = _Provider(
        ["not-json", _classification_wire(shard.pairs[0].candidate.pair_id, newer)],
        request_ids=["fixture:same", "fixture:same"],
    )

    with pytest.raises(InferenceExecutionFailed, match="provider evidence") as error:
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.LIVE),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=provider,
        )

    assert provider.calls == 2
    assert len(error.value.attempts) == 2
    assert "reused" in (error.value.attempts[1].validation_error or "")


def test_replay_is_provider_free_and_requires_exact_prior_live_evidence() -> None:
    live, _provider = _run_live_classification()
    workload, shard, *_ = _classification_fixture()
    resolver = _Resolver(live, [])

    replay = run_classification_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=resolver,
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )

    assert resolver.calls == [live.execution.receipt_artifact]
    assert replay.execution.receipt.mode == InferenceExecutionMode.REPLAY
    assert replay.execution.attempts == ()
    assert replay.execution.receipt.provider_request_id is None
    assert replay.classification_output == live.classification_output
    assert any(item.artifact == live.execution.receipt_artifact for item in replay.artifacts)


def test_replay_rejects_chain_receipt_substitution_and_contract_drift() -> None:
    live, _provider = _run_live_classification()
    workload, shard, *_ = _classification_fixture()
    resolver = _Resolver(live, [])
    replay = run_classification_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=resolver,
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )
    with pytest.raises(ValueError, match="prior LIVE"):
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.REPLAY),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            replay_resolver=_Resolver(replay, []),
            replay_source_receipt_artifact=replay.execution.receipt_artifact,
        )

    substituted = live.execution.receipt_artifact.model_copy(update={"sha256": "0" * 64})
    with pytest.raises((ValidationError, ValueError)):
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.REPLAY),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            replay_resolver=_Resolver(live, []),
            replay_source_receipt_artifact=substituted,
        )

    with pytest.raises(ValueError, match="prompts bytes"):
        run_classification_inference(
            contract=_contract(InferenceExecutionMode.REPLAY),
            workload=workload,
            input_shard=shard,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=b"changed prompt",
            response_schema_bytes=SCHEMA,
            replay_resolver=_Resolver(live, []),
            replay_source_receipt_artifact=live.execution.receipt_artifact,
        )


def test_wrong_task_coverage_and_reversed_supersession_fail_closed() -> None:
    workload, shard, _newer, older = _classification_fixture()
    wrong_task = json.dumps({"schema_version": 1, "task": "dependency", "decisions": []})
    missing = json.dumps({"schema_version": 1, "task": "classification", "decisions": []})
    reversed_direction = _classification_wire(shard.pairs[0].candidate.pair_id, older)
    for raw in (wrong_task, missing, reversed_direction):
        with pytest.raises(InferenceExecutionFailed):
            run_classification_inference(
                contract=_contract(InferenceExecutionMode.LIVE),
                workload=workload,
                input_shard=shard,
                algorithm_manifest_bytes=ALGORITHM,
                prompt_bytes=PROMPT,
                response_schema_bytes=SCHEMA,
                provider=_Provider([raw, raw]),
            )


def test_dependency_rejects_a_non_member_shard_before_provider_call() -> None:
    workload, shard, candidate_id, start, end = _dependency_fixture()
    changed_text = shard.downstream_note.source_note_utf8 + "Additional unrelated line.\n"
    changed_note = CanonicalSourceNoteSnapshot.create(
        document=shard.downstream_note.document,
        source_note_path=shard.downstream_note.source_note_path,
        source_note_utf8=changed_text,
        body_start_char=shard.downstream_note.body_start_char,
    )
    substituted = DependencyInferenceShard.create(
        downstream_note=changed_note,
        downstream_claim_revisions=(),
        temporal_resolution=shard.temporal_resolution,
        candidates=shard.candidates,
    )
    wire = json.dumps(
        {
            "schema_version": 1,
            "task": "dependency",
            "decisions": [
                {
                    "candidate_id": candidate_id,
                    "disposition": "DEPENDS_ON",
                    "dependency_kind": "summarizes",
                    "selected_downstream_claim_revision_ids": [],
                    "spans": [{"start_char": start, "end_char": end}],
                    "rationale": "The note repeats the earlier policy.",
                    "confidence": 0.9,
                }
            ],
        }
    )
    provider = _Provider([wire])

    with pytest.raises(ValueError, match="exact workload member"):
        run_dependency_inference(
            contract=_contract(InferenceExecutionMode.LIVE),
            workload=workload,
            input_shard=substituted,
            algorithm_manifest_bytes=ALGORITHM,
            prompt_bytes=PROMPT,
            response_schema_bytes=SCHEMA,
            provider=provider,
        )

    assert provider.calls == 0


def test_dependency_replay_rejects_missing_result_from_two_candidate_shard() -> None:
    workload, shard = _two_candidate_dependency_fixture()
    candidate = shard.candidates[0]
    classification = DependencyClassification.create(
        input_shard=shard,
        candidate=candidate,
        disposition=DependencyDisposition.NOT_DEPENDENT,
        rationale="This document does not rely on that governing policy.",
        confidence=0.8,
    )
    incomplete_payload = {
        "namespace": "mastervault.dependency-output-shard.v1",
        "schema_version": 1,
        "workload_id": workload.index.workload_id,
        "workload_sha256": workload.index.workload_sha256,
        "input_shard_id": shard.shard_id,
        "input_shard_sha256": shard.shard_sha256,
        "classifications": [classification.model_dump(mode="json")],
    }

    with pytest.raises(ValueError, match="every shard candidate exactly once"):
        _rehydrate_dependency_output(
            canonical_json_bytes(incomplete_payload).decode("utf-8"),
            workload=workload,
            shard=shard,
        )


def test_input_envelope_rejects_self_consistent_substituted_locator() -> None:
    live, _provider = _run_live_classification()
    envelope = live.execution.input_envelope
    prompt_ref = next(item for item in envelope.input_artifacts if "/prompts/" in item.path)
    substituted = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path=f"inference/prompts/{prompt_ref.sha256}.json",
        sha256=prompt_ref.sha256,
        byte_count=prompt_ref.byte_count,
    )
    refs = tuple(
        sorted(
            (substituted if item == prompt_ref else item for item in envelope.input_artifacts),
            key=lambda item: item.artifact_id,
        )
    )
    values = envelope.model_dump(mode="json", exclude={"envelope_id", "envelope_sha256"})
    values["input_artifacts"] = [item.model_dump(mode="json") for item in refs]
    digest = _payload_sha(values)

    with pytest.raises(ValidationError, match="exact contract/input locators"):
        InferenceInputEnvelope.model_validate_json(
            canonical_json_bytes(
                {
                    **values,
                    "envelope_id": f"inference-input:{digest}",
                    "envelope_sha256": digest,
                }
            )
        )


@pytest.mark.parametrize(
    ("field", "root"),
    (("raw_output_artifact", "raw"), ("validated_output_artifact", "outputs")),
)
def test_attempt_rejects_self_consistent_substituted_output_locator(
    field: str,
    root: str,
) -> None:
    live, _provider = _run_live_classification()
    attempt = live.execution.attempts[0]
    original = getattr(attempt, field)
    assert isinstance(original, ManagedArtifactRef)
    substituted = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_OUTPUT,
        path=f"inference/substituted-{root}/{original.sha256}.json",
        sha256=original.sha256,
        byte_count=original.byte_count,
    )
    values = attempt.model_dump(mode="json", exclude={"attempt_id", "attempt_sha256"})
    values[field] = substituted.model_dump(mode="json")
    digest = _payload_sha(values)

    with pytest.raises(ValidationError, match="exact content locator"):
        InferenceAttemptEvidence.model_validate_json(
            canonical_json_bytes(
                {
                    **values,
                    "attempt_id": f"inference-attempt:{digest}",
                    "attempt_sha256": digest,
                }
            )
        )


def test_outcome_requires_all_input_and_replay_source_evidence_bytes() -> None:
    live, _provider = _run_live_classification()
    live_values = live.model_dump(mode="json")
    input_id = live.execution.input_envelope.input_artifacts[0].artifact_id
    live_values["artifacts"] = [
        item for item in live_values["artifacts"] if item["artifact"]["artifact_id"] != input_id
    ]
    with pytest.raises(ValidationError, match="seven or eight|exactly the required"):
        RecordedInferenceOutcome.model_validate_json(canonical_json_bytes(live_values))

    workload, shard, *_ = _classification_fixture()
    replay = run_classification_inference(
        contract=_contract(InferenceExecutionMode.REPLAY),
        workload=workload,
        input_shard=shard,
        algorithm_manifest_bytes=ALGORITHM,
        prompt_bytes=PROMPT,
        response_schema_bytes=SCHEMA,
        replay_resolver=_Resolver(live, []),
        replay_source_receipt_artifact=live.execution.receipt_artifact,
    )
    replay_values = replay.model_dump(mode="json")
    source_id = live.execution.receipt_artifact.artifact_id
    replay_values["artifacts"] = [
        item for item in replay_values["artifacts"] if item["artifact"]["artifact_id"] != source_id
    ]
    with pytest.raises(ValidationError, match="seven or eight|exactly the required"):
        RecordedInferenceOutcome.model_validate_json(canonical_json_bytes(replay_values))


def test_outcome_rejects_extra_evidence_even_below_artifact_count_limit() -> None:
    live, _provider = _run_live_classification()
    extra_bytes = b'{"score":1}'
    extra_ref = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path=f"inference/extras/{_sha(extra_bytes)}.json",
        sha256=_sha(extra_bytes),
        byte_count=len(extra_bytes),
    )
    extra = InferenceArtifactPayload(
        artifact=extra_ref,
        content_utf8=extra_bytes.decode("utf-8"),
    )
    values = live.model_dump(mode="json")
    values["artifacts"] = sorted(
        [*values["artifacts"], extra.model_dump(mode="json")],
        key=lambda item: item["artifact"]["artifact_id"],
    )

    with pytest.raises(ValidationError, match="exactly the required inference evidence"):
        RecordedInferenceOutcome.model_validate_json(canonical_json_bytes(values))


def test_outcome_preflights_aggregate_content_before_nested_artifact_validation() -> None:
    oversized_piece = "x" * ((2 * 1024 * 1024) // 7 + 1)
    raw_artifacts = tuple({"artifact": {}, "content_utf8": oversized_piece} for _ in range(7))

    with pytest.raises(ValidationError, match="2 MiB v1 limit"):
        RecordedInferenceOutcome.model_validate({"artifacts": raw_artifacts})


def test_oversize_output_and_runtime_source_isolation() -> None:
    with pytest.raises(ValidationError, match="256 KiB"):
        ProviderCallResult(
            provider="fixture-provider",
            model="fixture-model",
            provider_request_id="fixture:oversize",
            raw_output_utf8="x" * (256 * 1024 + 1),
            usage=_usage(),
        )

    source = PACKAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PACKAGE_PATH))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        name == "mastervault.evals" or name.startswith("mastervault.evals.") for name in imported
    )
    assert "sl2" not in source.casefold()
    assert "gold" not in source.casefold()
