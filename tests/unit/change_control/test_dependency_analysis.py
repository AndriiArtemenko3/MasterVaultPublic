from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import mastervault.change_control as change_control_package
import mastervault.change_control.dependency_analysis as dependency_module
from mastervault.change_control.bootstrap import bootstrap_analysis_aggregate
from mastervault.change_control.classification import (
    ClaimPairClassification,
    ClassificationResultSet,
    ClassificationWorkload,
    select_classification_workload,
)
from mastervault.change_control.dependency_analysis import (
    MAX_DEPENDENCY_CANDIDATES_V1,
    MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1,
    CanonicalSourceNoteSnapshot,
    DependencyAnalysisLimitError,
    DependencyClassification,
    DependencyClassificationResultSet,
    DependencyDisposition,
    DependencyWorkload,
    SourceNoteInventory,
    derive_governing_supersessions,
    generate_dependency_workload,
    materialize_dependencies,
    validate_dependency_results,
    validate_dependency_workload,
)
from mastervault.change_control.discovery import (
    RelationshipCandidate,
    RelationshipCandidateSet,
    generate_relationship_candidates,
)
from mastervault.change_control.incoming import MANIFEST_RELATIVE_PATH
from mastervault.change_control.models import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    DependencyKind,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    DocumentSpanReference,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    PairDisposition,
    RelationGraph,
    TemporalConstraintSet,
    TemporalState,
    VersionedClaimRevision,
    aggregate_sha256,
    canonical_json_bytes,
)
from mastervault.change_control.seed import (
    load_verified_prechange_seed_manifest,
    verify_seed_document_context,
)
from mastervault.change_control.store import ChangeControlSnapshot, SqliteChangeControlStore

SHA_A = "a" * 64


def _document(
    key: str,
    *,
    family: str,
    start: date,
    end: date | None = None,
) -> DocumentVersionMetadata:
    return DocumentVersionMetadata.create(
        document_id=key,
        document_family=family,
        version_label=key,
        source_path=f"runtime/raw/{key}.md",
        source_sha256=SHA_A,
        declared_effective_from=start,
        declared_effective_to=end,
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )


def _note_text(key: str) -> str:
    return f"---\ntitle: {key}\n---\nBody for {key}. The legacy return window is thirty days.\n"


def _note(
    document: DocumentVersionMetadata,
    *,
    text: str | None = None,
) -> CanonicalSourceNoteSnapshot:
    exact = text if text is not None else _note_text(document.document_id)
    return CanonicalSourceNoteSnapshot.create(
        document=document,
        source_note_path=f"runtime/sources/{document.document_id}.md",
        source_note_utf8=exact,
        body_start_char=exact.index("Body"),
    )


def _claim(
    document: DocumentVersionMetadata,
    note: CanonicalSourceNoteSnapshot,
    local_id: str,
    *,
    statement: str,
) -> VersionedClaimRevision:
    return VersionedClaimRevision.create(
        document=document,
        source=ClaimSourceReference(
            source_note_path=note.source_note_path,
            source_note_sha256=note.source_note_sha256,
            source_claim_id=local_id,
        ),
        statement=statement,
        declared_effective_from=document.declared_effective_from,
        declared_effective_to=document.declared_effective_to,
        scopes=("policy.returns",),
    )


@dataclass(frozen=True)
class _Capability:
    inventory: SourceNoteInventory

    def verify(self, *, snapshot: ChangeControlSnapshot) -> SourceNoteInventory:
        assert snapshot.aggregate.aggregate_id == self.inventory.aggregate_id
        return self.inventory


@dataclass(frozen=True)
class _Fixture:
    snapshot: ChangeControlSnapshot
    candidates: RelationshipCandidateSet
    classifier_workload: ClassificationWorkload
    classifier_results: ClassificationResultSet
    capability: _Capability
    changed: VersionedClaimRevision
    older: tuple[VersionedClaimRevision, ...]
    notes: tuple[CanonicalSourceNoteSnapshot, ...]


def _classification(
    candidate: RelationshipCandidate,
    snapshot: ChangeControlSnapshot,
    *,
    supersedes: bool,
) -> ClaimPairClassification:
    revisions = {item.claim_revision_id: item for item in snapshot.aggregate.claims.revisions}
    return ClaimPairClassification.create(
        candidate=candidate,
        endpoint_revisions=tuple(revisions[item] for item in candidate.claim_revision_ids),
        disposition=PairDisposition.SUPERSEDES if supersedes else PairDisposition.UNRELATED,
        rationale="Synthetic provider fixture classification.",
        confidence=0.9,
        newer_revision_id=(candidate.changed_claim_revision_id if supersedes else None),
    )


def _fixture(
    *,
    downstream_count: int = 4,
    oversized_note: bool = False,
    extract_downstream_claims: bool = True,
    first_note_body_chars: int = 0,
) -> _Fixture:
    changed_doc = _document("returns-v2", family="policy.returns", start=date(2026, 1, 12))
    older_doc = _document("returns-v1", family="policy.returns", start=date(2024, 1, 15))
    changed_note = _note(changed_doc)
    older_note = _note(older_doc)
    changed = _claim(
        changed_doc,
        changed_note,
        "policy-returns-v2-01",
        statement="Customers may return an item within forty five days.",
    )
    older = (
        _claim(
            older_doc,
            older_note,
            "policy-returns-v1-01",
            statement="Customers may return an item within thirty days.",
        ),
        _claim(
            older_doc,
            older_note,
            "policy-returns-v1-02",
            statement="Opened returns use the thirty day return window.",
        ),
    )
    downstream_documents: list[DocumentVersionMetadata] = []
    downstream_notes: list[CanonicalSourceNoteSnapshot] = []
    downstream_claims: list[VersionedClaimRevision] = []
    names = ["faq", "showroom", "expired-proposal", "grading-control"]
    for index in range(downstream_count):
        key = names[index] if index < len(names) else f"downstream-{index:02d}"
        end = date(2025, 10, 19) if key == "expired-proposal" else None
        document = _document(
            key,
            family=f"operations.{key}",
            start=date(2025, 1, 1),
            end=end,
        )
        text = None
        if oversized_note and index == 0:
            text = "---\ntitle: huge\n---\nBody " + ("x" * 270_000)
        elif first_note_body_chars and index == 0:
            text = "---\ntitle: dense\n---\nBody " + ("x" * first_note_body_chars)
        note = _note(document, text=text)
        downstream_documents.append(document)
        downstream_notes.append(note)
        # The showroom deliberately has no extracted claim.  Its useful evidence
        # exists only in the complete SourceNote body.
        if extract_downstream_claims and key != "showroom":
            downstream_claims.append(
                _claim(
                    document,
                    note,
                    f"{key}-01",
                    statement=f"The {key} operational statement remains separately recorded.",
                )
            )
    documents = (changed_doc, older_doc, *downstream_documents)
    claims = (changed, *older, *downstream_claims)
    aggregate = ChangeControlAggregate.create(
        aggregate_id="dependency-fixture",
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    snapshot = ChangeControlSnapshot(
        aggregate=aggregate,
        revision=2,
        aggregate_sha256=aggregate_sha256(aggregate),
    )
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=(changed.claim_revision_id,),
        as_of=date(2026, 2, 1),
    )
    classifier_workload = select_classification_workload(snapshot, candidates=candidates)
    older_ids = {item.claim_revision_id for item in older}
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    classifications = tuple(
        _classification(
            candidate_by_id[item.pair_id],
            snapshot,
            supersedes=(candidate_by_id[item.pair_id].incumbent_claim_revision_id in older_ids),
        )
        for item in classifier_workload.selected
    )
    classifier_results = ClassificationResultSet.create(
        workload=classifier_workload,
        classifications=classifications,
    )
    notes = (changed_note, older_note, *downstream_notes)
    inventory = SourceNoteInventory.create(snapshot=snapshot, notes=notes)
    return _Fixture(
        snapshot=snapshot,
        candidates=candidates,
        classifier_workload=classifier_workload,
        classifier_results=classifier_results,
        capability=_Capability(inventory),
        changed=changed,
        older=older,
        notes=notes,
    )


def _workload(fixture: _Fixture) -> DependencyWorkload:
    return generate_dependency_workload(
        fixture.snapshot,
        candidates=fixture.candidates,
        classification_results=fixture.classifier_results,
        inventory_capability=fixture.capability,
    )


def test_governing_supersession_derivation_accepts_mixed_non_edges_and_rejects_edges() -> None:
    fixture = _fixture()
    assert len(derive_governing_supersessions(fixture.classifier_results)) == 2

    revisions = {
        item.claim_revision_id: item for item in fixture.snapshot.aggregate.claims.revisions
    }
    dispositions = (
        PairDisposition.UNRELATED,
        PairDisposition.COEXISTS,
        PairDisposition.CONTRADICTS,
    )
    candidates = {item.pair_id: item for item in fixture.candidates.candidates}
    mixed = tuple(
        ClaimPairClassification.create(
            candidate=candidates[item.pair_id],
            endpoint_revisions=tuple(
                revisions[revision_id]
                for revision_id in candidates[item.pair_id].claim_revision_ids
            ),
            disposition=dispositions[index % len(dispositions)],
            rationale="Synthetic non-governing mixed classification.",
            confidence=0.9,
        )
        for index, item in enumerate(fixture.classifier_workload.selected)
    )
    mixed_results = ClassificationResultSet.create(
        workload=fixture.classifier_workload,
        classifications=mixed,
    )
    assert derive_governing_supersessions(mixed_results) == ()


def test_complete_inventory_includes_body_only_expired_and_control_documents() -> None:
    fixture = _fixture()
    workload = _workload(fixture)

    assert len(workload.index.governing_supersessions) == 2
    assert len(workload.input_shards) == 4
    assert len(workload.candidates) == 8
    assert len(workload.exclusions) == 4
    by_document = {
        shard.downstream_note.document.document_id: shard for shard in workload.input_shards
    }
    assert set(by_document) == {"faq", "showroom", "expired-proposal", "grading-control"}
    assert by_document["showroom"].downstream_claim_revisions == ()
    assert len(by_document["showroom"].candidates) == 2
    assert by_document["expired-proposal"].temporal_resolution.state == TemporalState.EXPIRED
    assert by_document["grading-control"].temporal_resolution.state == TemporalState.CURRENT
    assert all(len(shard.canonical_bytes()) <= 256 * 1024 for shard in workload.input_shards)


def test_shipped_runtime_inventory_includes_all_six_eligible_documents(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    store = SqliteChangeControlStore(tmp_path / "dependency-scenario.sqlite3")
    store.init_schema()
    try:
        bootstrap = bootstrap_analysis_aggregate(
            repo_root=repo_root,
            prechange_manifest_path=(
                repo_root / "datasets/larkstead/change_control/sl2_prechange.yaml"
            ),
            incoming_manifest_path=repo_root / MANIFEST_RELATIVE_PATH,
            store=store,
            prechange_operation_id="dependency-test:prechange",
            analysis_operation_id="dependency-test:analysis",
        )
    finally:
        store.close()
    candidates = generate_relationship_candidates(
        bootstrap.snapshot,
        changed_claim_revision_ids=bootstrap.binding.changed_claim_revision_ids,
        as_of=bootstrap.binding.analysis_as_of,
    )
    classifier_workload = select_classification_workload(bootstrap.snapshot, candidates=candidates)
    claims_by_source_id = {
        item.source.source_claim_id: item for item in bootstrap.snapshot.aggregate.claims.revisions
    }
    governing_endpoint_sets = {
        frozenset(
            {
                claims_by_source_id[f"policy-sl2-policy-returns-v2-{index:02d}"].claim_revision_id,
                claims_by_source_id[f"policy-sl2-policy-returns-v1-{index:02d}"].claim_revision_id,
            }
        )
        for index in range(1, 11)
    }
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    classifications = tuple(
        _classification(
            candidate_by_id[item.pair_id],
            bootstrap.snapshot,
            supersedes=(
                frozenset(candidate_by_id[item.pair_id].claim_revision_ids)
                in governing_endpoint_sets
            ),
        )
        for item in classifier_workload.selected
    )
    classifier_results = ClassificationResultSet.create(
        workload=classifier_workload,
        classifications=classifications,
    )

    seed_manifest = load_verified_prechange_seed_manifest(
        repo_root / "datasets/larkstead/change_control/sl2_prechange.yaml"
    )
    note_snapshots = []
    for seed_document in seed_manifest.manifest.documents:
        context = verify_seed_document_context(
            repo_root=repo_root,
            manifest_context=seed_manifest,
            document_id=seed_document.document_id,
        )
        note_snapshots.append(
            CanonicalSourceNoteSnapshot.create(
                document=context.document,
                source_note_path=context.source_note_path,
                source_note_utf8=context.note_text,
                body_start_char=context.body_start_char,
            )
        )
    incoming_text = bootstrap.incoming_event.processed_snapshot.decode("utf-8")
    frontmatter_end = incoming_text.index("\n---\n", 4) + len("\n---\n")
    incoming_claim = bootstrap.incoming_event.claim_revisions[0]
    note_snapshots.append(
        CanonicalSourceNoteSnapshot.create(
            document=bootstrap.incoming_event.document,
            source_note_path=incoming_claim.source.source_note_path,
            source_note_utf8=incoming_text,
            body_start_char=frontmatter_end,
        )
    )
    capability = _Capability(
        SourceNoteInventory.create(
            snapshot=bootstrap.snapshot,
            notes=tuple(note_snapshots),
        )
    )

    workload = generate_dependency_workload(
        bootstrap.snapshot,
        candidates=candidates,
        classification_results=classifier_results,
        inventory_capability=capability,
    )

    assert {item.downstream_note.document.document_id for item in workload.input_shards} == {
        "sl2-memo-holiday-exception",
        "sl2-faq-returns",
        "sl2-macros-returns-helprise",
        "process-showroom-demo-unit-rotation",
        "sop-returns-receiving-restock-grading",
        "sl3-proposal-v1",
    }
    assert len(workload.index.governing_supersessions) == 10
    assert len(workload.candidates) == 60
    assert len(workload.exclusions) == 20
    assert len(workload.input_shards) == 6
    assert max(len(item.canonical_bytes()) for item in workload.input_shards) < 256 * 1024
    assert sum(len(item.canonical_bytes()) for item in workload.input_shards) < 1024 * 1024


def test_workload_replay_is_deterministic_and_revalidates_exactly() -> None:
    fixture = _fixture()
    first = _workload(fixture)
    second = _workload(fixture)

    assert first == second
    assert (
        validate_dependency_workload(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=first,
            inventory_capability=fixture.capability,
        )
        == first
    )


@pytest.mark.parametrize("tamper", ["omission", "unknown", "duplicate", "hash-swap"])
def test_workload_ledger_tampering_fails_closed(tamper: str) -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    if tamper == "omission":
        forged = workload.model_copy(update={"input_shards": workload.input_shards[:-1]})
    elif tamper == "unknown":
        forged_candidate = (
            workload.input_shards[0]
            .candidates[0]
            .model_copy(update={"downstream_document_version_id": "docv:" + "f" * 64})
        )
        forged_shard = workload.input_shards[0].model_copy(
            update={"candidates": (forged_candidate, *workload.input_shards[0].candidates[1:])}
        )
        forged = workload.model_copy(
            update={"input_shards": (forged_shard, *workload.input_shards[1:])}
        )
    elif tamper == "duplicate":
        forged = workload.model_copy(
            update={"input_shards": (*workload.input_shards, workload.input_shards[0])}
        )
    else:
        swapped = workload.input_shards[0].model_copy(
            update={"shard_sha256": workload.input_shards[1].shard_sha256}
        )
        forged = workload.model_copy(update={"input_shards": (swapped, *workload.input_shards[1:])})

    with pytest.raises((ValidationError, ValueError)):
        validate_dependency_workload(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=forged,
            inventory_capability=fixture.capability,
        )


def test_stale_snapshot_or_inventory_capability_fails_closed() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    stale_inventory = fixture.capability.inventory.model_copy(
        update={"snapshot_revision": fixture.snapshot.revision + 1}
    )

    with pytest.raises((ValidationError, ValueError)):
        validate_dependency_workload(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=workload,
            inventory_capability=_Capability(stale_inventory),
        )


def test_no_graph_valid_changed_to_older_anchor_is_rejected() -> None:
    fixture = _fixture()
    candidate_by_id = {item.pair_id: item for item in fixture.candidates.candidates}
    unrelated = tuple(
        _classification(candidate_by_id[item.pair_id], fixture.snapshot, supersedes=False)
        for item in fixture.classifier_workload.selected
    )
    results = ClassificationResultSet.create(
        workload=fixture.classifier_workload,
        classifications=unrelated,
    )

    with pytest.raises(ValueError, match="no graph-valid"):
        generate_dependency_workload(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=results,
            inventory_capability=fixture.capability,
        )


def test_global_cross_product_over_64_fails_before_output() -> None:
    fixture = _fixture(downstream_count=33, extract_downstream_claims=False)

    with pytest.raises(DependencyAnalysisLimitError) as error:
        _workload(fixture)

    assert error.value.category == "dependency-candidate-cross-product"
    assert error.value.observed == 66
    assert error.value.limit == MAX_DEPENDENCY_CANDIDATES_V1


def test_every_changed_document_is_excluded_for_every_governing_root() -> None:
    documents = (
        _document("changed-a", family="policy.a", start=date(2026, 1, 2)),
        _document("changed-b", family="policy.b", start=date(2026, 1, 3)),
        _document("older-a", family="policy.a", start=date(2024, 1, 2)),
        _document("older-b", family="policy.b", start=date(2024, 1, 3)),
        _document("target", family="support.target", start=date(2025, 1, 1)),
    )
    notes = tuple(_note(item) for item in documents)
    claims = tuple(
        _claim(
            document,
            note,
            f"multi-root-{index:02d}",
            statement=f"Multi root policy statement number {index} remains applicable.",
        )
        for index, (document, note) in enumerate(zip(documents, notes, strict=True), start=1)
    )
    aggregate = ChangeControlAggregate.create(
        aggregate_id="multi-changed-fixture",
        documents=DocumentVersionRegistry.create(documents),
        claims=ClaimRevisionRegistry.create(claims),
        relation_graph=RelationGraph.create(()),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create(()),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    snapshot = ChangeControlSnapshot(
        aggregate=aggregate,
        revision=2,
        aggregate_sha256=aggregate_sha256(aggregate),
    )
    changed = (claims[0], claims[1])
    candidates = generate_relationship_candidates(
        snapshot,
        changed_claim_revision_ids=tuple(item.claim_revision_id for item in changed),
        as_of=date(2026, 2, 1),
    )
    classifier_workload = select_classification_workload(snapshot, candidates=candidates)
    candidate_by_id = {item.pair_id: item for item in candidates.candidates}
    governing_pairs = {
        (claims[0].claim_revision_id, claims[2].claim_revision_id),
        (claims[1].claim_revision_id, claims[3].claim_revision_id),
    }
    classifications = tuple(
        _classification(
            candidate_by_id[item.pair_id],
            snapshot,
            supersedes=(
                (
                    candidate_by_id[item.pair_id].changed_claim_revision_id,
                    candidate_by_id[item.pair_id].incumbent_claim_revision_id,
                )
                in governing_pairs
            ),
        )
        for item in classifier_workload.selected
    )
    classifier_results = ClassificationResultSet.create(
        workload=classifier_workload,
        classifications=classifications,
    )
    capability = _Capability(SourceNoteInventory.create(snapshot=snapshot, notes=notes))

    workload = generate_dependency_workload(
        snapshot,
        candidates=candidates,
        classification_results=classifier_results,
        inventory_capability=capability,
    )

    changed_document_ids = {item.document.document_version_id for item in changed}
    assert not changed_document_ids & {
        item.downstream_note.document.document_version_id for item in workload.input_shards
    }
    changed_exclusions = [
        item
        for item in workload.exclusions
        if dependency_module.DependencyCandidateExclusionReason.CHANGED_DOCUMENT in item.reasons
    ]
    assert len(changed_exclusions) == 4


def test_one_oversized_full_document_is_rejected_without_chunking() -> None:
    fixture = _fixture(oversized_note=True)

    with pytest.raises(DependencyAnalysisLimitError) as error:
        _workload(fixture)

    assert error.value.category == "complete-document-input-bytes"
    assert error.value.observed > MAX_DEPENDENCY_INPUT_SHARD_CANONICAL_BYTES_V1


def _span(shard: dependency_module.DependencyInferenceShard) -> DocumentSpanReference:
    note = shard.downstream_note
    quote = "The legacy return window is thirty days."
    start = note.source_note_utf8.index(quote)
    return DocumentSpanReference(
        document_version_id=note.document.document_version_id,
        source_note_path=note.source_note_path,
        source_note_sha256=note.source_note_sha256,
        quote=quote,
        start_char=start,
        end_char=start + len(quote),
    )


def test_positive_and_negative_result_shapes_are_strict() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    shard = workload.input_shards[0]
    candidate = shard.candidates[0]

    with pytest.raises(ValidationError, match="requires a dependency kind"):
        DependencyClassification.model_validate(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_sha256": candidate.candidate_sha256,
                "disposition": "DEPENDS_ON",
                "rationale": "Missing edge evidence.",
                "confidence": 0.5,
                "classification_id": "depclass:" + "a" * 64,
                "classification_sha256": "a" * 64,
            }
        )
    with pytest.raises(ValidationError, match="must not carry"):
        DependencyClassification.model_validate(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_sha256": candidate.candidate_sha256,
                "disposition": "NOT_DEPENDENT",
                "dependency_kind": "quotes",
                "downstream_spans": [_span(shard).model_dump(mode="json")],
                "rationale": "Contradictory negative shape.",
                "confidence": 0.5,
                "classification_id": "depclass:" + "a" * 64,
                "classification_sha256": "a" * 64,
            }
        )


def test_span_source_note_and_exact_slice_mismatch_are_rejected() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    shard = workload.input_shards[0]
    candidate = shard.candidates[0]
    exact = _span(shard)
    wrong_sha = exact.model_copy(update={"source_note_sha256": "f" * 64})
    wrong_quote = exact.model_copy(update={"quote": "x" * len(exact.quote)})

    for forged in (wrong_sha, wrong_quote):
        with pytest.raises(ValueError):
            DependencyClassification.create(
                input_shard=shard,
                candidate=candidate,
                disposition=DependencyDisposition.DEPENDS_ON,
                dependency_kind=DependencyKind.QUOTES,
                downstream_spans=(forged,),
                rationale="Exact evidence should be required.",
                confidence=0.9,
            )


def test_frontmatter_span_is_rejected_but_kind_is_not_derived_from_document_state() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    current_shard = next(
        item
        for item in workload.input_shards
        if item.temporal_resolution.state == TemporalState.CURRENT
    )
    candidate = current_shard.candidates[0]
    frontmatter_quote = "title"
    frontmatter_start = current_shard.downstream_note.source_note_utf8.index(frontmatter_quote)
    frontmatter_span = DocumentSpanReference(
        document_version_id=current_shard.downstream_note.document.document_version_id,
        source_note_path=current_shard.downstream_note.source_note_path,
        source_note_sha256=current_shard.downstream_note.source_note_sha256,
        quote=frontmatter_quote,
        start_char=frontmatter_start,
        end_char=frontmatter_start + len(frontmatter_quote),
    )

    with pytest.raises(ValueError, match="body evidence"):
        DependencyClassification.create(
            input_shard=current_shard,
            candidate=candidate,
            disposition=DependencyDisposition.DEPENDS_ON,
            dependency_kind=DependencyKind.QUOTES,
            downstream_spans=(frontmatter_span,),
            rationale="Frontmatter must never ground an edge.",
            confidence=0.9,
        )
    historical_reference = DependencyClassification.create(
        input_shard=current_shard,
        candidate=candidate,
        disposition=DependencyDisposition.DEPENDS_ON,
        dependency_kind=DependencyKind.HISTORICAL_REFERENCE,
        downstream_spans=(_span(current_shard),),
        rationale="A current document may contain a historical-only reference.",
        confidence=0.9,
    )
    assert historical_reference.dependency_kind == DependencyKind.HISTORICAL_REFERENCE


def test_span_count_and_individual_span_byte_limits_fail_closed() -> None:
    count_fixture = _fixture(first_note_body_chars=200)
    count_workload = _workload(count_fixture)
    count_shard = next(
        item
        for item in count_workload.input_shards
        if item.downstream_note.document.document_id == "faq"
    )
    note = count_shard.downstream_note
    first_x = note.source_note_utf8.index("x")
    spans = tuple(
        DocumentSpanReference(
            document_version_id=note.document.document_version_id,
            source_note_path=note.source_note_path,
            source_note_sha256=note.source_note_sha256,
            quote="x",
            start_char=first_x + index,
            end_char=first_x + index + 1,
        )
        for index in range(65)
    )
    with pytest.raises(DependencyAnalysisLimitError) as count_error:
        DependencyClassification.create(
            input_shard=count_shard,
            candidate=count_shard.candidates[0],
            disposition=DependencyDisposition.DEPENDS_ON,
            dependency_kind=DependencyKind.QUOTES,
            downstream_spans=spans,
            rationale="Too many evidence spans must fail closed.",
            confidence=0.9,
        )
    assert count_error.value.category == "spans-per-dependency"

    size_fixture = _fixture(first_note_body_chars=20_000)
    size_workload = _workload(size_fixture)
    size_shard = next(
        item
        for item in size_workload.input_shards
        if item.downstream_note.document.document_id == "faq"
    )
    size_note = size_shard.downstream_note
    start = size_note.source_note_utf8.index("x")
    quote = "x" * 17_000
    huge_span = DocumentSpanReference(
        document_version_id=size_note.document.document_version_id,
        source_note_path=size_note.source_note_path,
        source_note_sha256=size_note.source_note_sha256,
        quote=quote,
        start_char=start,
        end_char=start + len(quote),
    )
    with pytest.raises(DependencyAnalysisLimitError) as size_error:
        DependencyClassification.create(
            input_shard=size_shard,
            candidate=size_shard.candidates[0],
            disposition=DependencyDisposition.DEPENDS_ON,
            dependency_kind=DependencyKind.QUOTES,
            downstream_spans=(huge_span,),
            rationale="Oversized evidence spans must fail closed.",
            confidence=0.9,
        )
    assert size_error.value.category == "span-canonical-bytes"


def _complete_dependency_results(
    workload: DependencyWorkload,
) -> DependencyClassificationResultSet:
    classifications = []
    for shard in workload.input_shards:
        for root_index, candidate in enumerate(shard.candidates):
            positive = (
                shard.downstream_note.document.document_id in {"faq", "expired-proposal"}
                and root_index == 0
            )
            classifications.append(
                DependencyClassification.create(
                    input_shard=shard,
                    candidate=candidate,
                    disposition=(
                        DependencyDisposition.DEPENDS_ON
                        if positive
                        else DependencyDisposition.NOT_DEPENDENT
                    ),
                    dependency_kind=(
                        DependencyKind.HISTORICAL_REFERENCE
                        if positive and shard.temporal_resolution.state == TemporalState.EXPIRED
                        else DependencyKind.QUOTES
                        if positive
                        else None
                    ),
                    selected_downstream_claim_revision_ids=(
                        (shard.downstream_claim_revisions[0].claim_revision_id,)
                        if positive and shard.downstream_claim_revisions
                        else ()
                    ),
                    downstream_spans=(_span(shard),) if positive else (),
                    rationale=(
                        "Exact downstream evidence supports dependency."
                        if positive
                        else "The complete note does not depend on the old claim."
                    ),
                    confidence=0.9,
                )
            )
    return DependencyClassificationResultSet.create(
        workload=workload,
        classifications=tuple(classifications),
    )


def test_results_revalidate_and_materialize_only_exact_positive_edges() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    results = _complete_dependency_results(workload)

    assert (
        validate_dependency_results(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=workload,
            results=results,
            inventory_capability=fixture.capability,
        )
        == results
    )
    assessments = materialize_dependencies(
        fixture.snapshot,
        candidates=fixture.candidates,
        classification_results=fixture.classifier_results,
        workload=workload,
        results=results,
        inventory_capability=fixture.capability,
    )
    assert len(assessments) == 2
    assert assessments == tuple(sorted(assessments, key=lambda item: item.dependency_id))
    assert all(item.upstream in fixture.older for item in assessments)
    assert any(item.dependency_kind == DependencyKind.HISTORICAL_REFERENCE for item in assessments)


@pytest.mark.parametrize("tamper", ["omit", "duplicate", "input-swap", "output-hash"])
def test_result_coverage_and_shard_substitution_fail_closed(tamper: str) -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    results = _complete_dependency_results(workload)
    if tamper == "omit":
        forged = results.model_copy(update={"output_shards": results.output_shards[:-1]})
    elif tamper == "duplicate":
        forged = results.model_copy(
            update={"output_shards": (*results.output_shards, results.output_shards[0])}
        )
    elif tamper == "input-swap":
        swapped = results.output_shards[0].model_copy(
            update={"input_shard_sha256": results.output_shards[1].input_shard_sha256}
        )
        forged = results.model_copy(update={"output_shards": (swapped, *results.output_shards[1:])})
    else:
        swapped = results.output_shards[0].model_copy(update={"output_shard_sha256": "f" * 64})
        forged = results.model_copy(update={"output_shards": (swapped, *results.output_shards[1:])})

    with pytest.raises((ValidationError, ValueError)):
        validate_dependency_results(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=workload,
            results=forged,
            inventory_capability=fixture.capability,
        )


def test_recomputed_output_shard_under_foreign_workload_is_rejected() -> None:
    fixture = _fixture()
    workload = _workload(fixture)
    results = _complete_dependency_results(workload)
    original = results.output_shards[0]
    foreign_workload_id = "depwork:" + "f" * 64
    foreign_workload_sha = "f" * 64
    payload = {
        "namespace": "mastervault.dependency-output-shard.v1",
        "schema_version": 1,
        "workload_id": foreign_workload_id,
        "workload_sha256": foreign_workload_sha,
        "input_shard_id": original.input_shard_id,
        "input_shard_sha256": original.input_shard_sha256,
        "classifications": [item.model_dump(mode="json") for item in original.classifications],
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    foreign = dependency_module.DependencyOutputShard(
        workload_id=foreign_workload_id,
        workload_sha256=foreign_workload_sha,
        input_shard_id=original.input_shard_id,
        input_shard_sha256=original.input_shard_sha256,
        classifications=original.classifications,
        output_shard_id=f"depout:{digest}",
        output_shard_sha256=digest,
    )
    forged = results.model_copy(update={"output_shards": (foreign, *results.output_shards[1:])})

    with pytest.raises(ValidationError, match="different result workload"):
        validate_dependency_results(
            fixture.snapshot,
            candidates=fixture.candidates,
            classification_results=fixture.classifier_results,
            workload=workload,
            results=forged,
            inventory_capability=fixture.capability,
        )


def test_runtime_module_is_scenario_and_golden_isolated() -> None:
    path = Path(dependency_module.__file__ or "")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert "datasets" not in source.casefold()
    assert "sl2" not in source.casefold()
    assert not any("gold" in module.casefold() for module in imported)


def test_dependency_contracts_are_exported_from_package_root() -> None:
    assert set(dependency_module.__all__) <= set(change_control_package.__all__)
    for name in dependency_module.__all__:
        assert getattr(change_control_package, name) is getattr(dependency_module, name)
