from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import mastervault.change_control.seed as seed_module
from mastervault.change_control import (
    DocumentAuthority,
    DocumentReplacementSet,
    DocumentRole,
    RelationGraph,
    SeedBoundaryError,
    SeedIntegrityError,
    SeedReuseError,
    TemporalConstraintSet,
    TemporalState,
    ValidatedTemporalConstraintSet,
    load_prechange_seed_manifest,
    load_verified_prechange_seed_manifest,
    materialize_prechange_seed,
    resolve_claim_revision,
    resolve_document_span,
    resolve_document_temporality,
    verify_seed_document_context,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"

EXPECTED_DOCUMENT_IDS = {
    "sl2-policy-returns-v1",
    "sl2-memo-holiday-exception",
    "sl2-faq-returns",
    "sl2-macros-returns-helprise",
    "process-showroom-demo-unit-rotation",
    "sop-returns-receiving-restock-grading",
    "sl3-proposal-v1",
}


def _manifest_data() -> dict:
    loaded = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _document(document_id: str):
    manifest = load_prechange_seed_manifest(MANIFEST)
    return next(item for item in manifest.documents if item.document_id == document_id)


def _verified_manifest(path: Path = MANIFEST):
    return load_verified_prechange_seed_manifest(path)


def _verified_context(
    document_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_context=None,
):
    return verify_seed_document_context(
        repo_root=repo_root,
        manifest_context=manifest_context or _verified_manifest(),
        document_id=document_id,
    )


def _empty_temporal_constraints() -> ValidatedTemporalConstraintSet:
    return ValidatedTemporalConstraintSet.create(
        constraints=TemporalConstraintSet.create(()),
        relation_graph=RelationGraph(assessments=()),
        document_replacements=DocumentReplacementSet.create(()),
    )


def _copy_binding(repo_root: Path, seed_document) -> Path:
    fake_repo = repo_root / "repo"
    for relative in (seed_document.source_path, seed_document.processed_path):
        destination = fake_repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return fake_repo


def _write_manifest(tmp_path: Path, data: dict, *, name: str = "seed.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_verified_prechange_seed_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_affected_document_ids", ["sl2-faq-returns"]),
        ("classification", "SUPERSEDES"),
        ("patches", [{"before": "30", "after": "45"}]),
        ("expected_review_decision", "approve"),
    ],
)
def test_manifest_rejects_evaluator_fields_at_any_depth(
    tmp_path: Path,
    field: str,
    value: object,
):
    data = _manifest_data()
    data["documents"][0][field] = value
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(SeedBoundaryError, match="evaluator"):
        load_prechange_seed_manifest(path)


def test_manifest_rejects_unknown_runtime_fields_and_direct_evaluator_labels(tmp_path: Path):
    data = _manifest_data()
    data["operator_note"] = "SUPERSEDES"
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(SeedBoundaryError, match="evaluator label"):
        load_prechange_seed_manifest(path)

    data = _manifest_data()
    data["operator_note"] = "ordinary metadata"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_prechange_seed_manifest(path)


def test_runtime_loader_refuses_any_evaluator_gold_directory(tmp_path: Path):
    gold_dir = tmp_path / "golden"
    gold_dir.mkdir()
    path = gold_dir / "seed.yaml"
    path.write_bytes(MANIFEST.read_bytes())
    with pytest.raises(SeedBoundaryError, match="evaluator-gold"):
        load_prechange_seed_manifest(path)

    uppercase_dir = tmp_path / "GOLDEN"
    uppercase_dir.mkdir(exist_ok=True)
    uppercase_path = uppercase_dir / "seed.yaml"
    uppercase_path.write_bytes(MANIFEST.read_bytes())
    with pytest.raises(SeedBoundaryError, match="evaluator-gold"):
        load_prechange_seed_manifest(uppercase_path)


def test_runtime_loader_rejects_case_alias_of_evaluator_gold_when_exposed(tmp_path: Path):
    gold_dir = tmp_path / "golden"
    gold_dir.mkdir()
    path = gold_dir / "seed.yaml"
    path.write_bytes(MANIFEST.read_bytes())
    alias = tmp_path / "GOLDEN" / "seed.yaml"
    try:
        same_file = alias.exists() and os.path.samefile(alias, path)
    except OSError:
        same_file = False
    if not same_file:
        pytest.skip("filesystem does not expose a case-insensitive GOLDEN alias")
    with pytest.raises(SeedBoundaryError, match="evaluator-gold"):
        load_prechange_seed_manifest(alias)


def test_manifest_rejects_casefolded_gold_component_below_runtime_root(tmp_path: Path):
    data = _manifest_data()
    data["documents"][0]["source_path"] = "datasets/larkstead/raw/GOLDEN/forbidden-source.md"
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises((SeedBoundaryError, ValidationError), match="evaluator-gold"):
        load_prechange_seed_manifest(path)


def test_manifest_uses_declared_half_open_metadata_and_keeps_v1_open():
    manifest = load_prechange_seed_manifest(MANIFEST)
    documents = {document.document_id: document for document in manifest.documents}

    assert manifest.as_of.isoformat() == "2026-01-11"
    assert set(documents) == EXPECTED_DOCUMENT_IDS
    assert documents["sl2-policy-returns-v1"].declared_effective_to is None
    proposal_end = documents["sl3-proposal-v1"].declared_effective_to
    assert proposal_end is not None and proposal_end.isoformat() == "2025-10-19"
    assert {
        document_id: (document.document_family, document.role.value, document.authority.value)
        for document_id, document in documents.items()
    } == {
        "sl2-policy-returns-v1": (
            "customer-support.returns-policy",
            "policy",
            "primary",
        ),
        "sl2-memo-holiday-exception": (
            "sl2-holiday-return-exception",
            "memo",
            "primary",
        ),
        "sl2-faq-returns": ("sl2-returns-faq", "faq", "delegated"),
        "sl2-macros-returns-helprise": ("sl2-returns-macros", "sop", "delegated"),
        "process-showroom-demo-unit-rotation": (
            "showroom-demo-unit-rotation",
            "process",
            "delegated",
        ),
        "sop-returns-receiving-restock-grading": (
            "returns-receiving-restock-grading",
            "sop",
            "delegated",
        ),
        "sl3-proposal-v1": ("sl3-cobalt-proposal", "proposal", "transactional"),
    }
    assert {document.role for document in manifest.documents} == {
        DocumentRole.POLICY,
        DocumentRole.MEMO,
        DocumentRole.FAQ,
        DocumentRole.SOP,
        DocumentRole.PROCESS,
        DocumentRole.PROPOSAL,
    }
    serialized = manifest.model_dump_json()
    for forbidden in (
        "expected_",
        "classification",
        "affected",
        "patches",
        "review_decision",
        "sl2-policy-returns-v2",
    ):
        assert forbidden not in serialized

    proposal = documents["sl3-proposal-v1"].document_version()
    constraints = _empty_temporal_constraints()
    assert (
        resolve_document_temporality(proposal, constraints, as_of=date(2025, 10, 18)).state
        == TemporalState.CURRENT
    )
    assert (
        resolve_document_temporality(proposal, constraints, as_of=date(2025, 10, 19)).state
        == TemporalState.EXPIRED
    )


def test_manifest_rejects_zero_length_declared_interval(tmp_path: Path):
    data = _manifest_data()
    data["documents"][0]["declared_effective_to"] = data["documents"][0]["declared_effective_from"]
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError, match="must follow"):
        load_prechange_seed_manifest(path)


def test_verified_context_binds_exact_manifest_paths_hashes_and_provenance():
    seed_document = _document("sl2-policy-returns-v1")
    manifest_context = _verified_manifest()
    context = _verified_context(
        seed_document.document_id,
        manifest_context=manifest_context,
    )

    assert context.manifest_context == manifest_context
    assert context.document == seed_document.document_version()
    assert context.source_path == (REPO_ROOT / seed_document.source_path).resolve()
    assert context.source_note_disk_path == (REPO_ROOT / seed_document.processed_path).resolve()
    assert context.source_note_path == ("customer-support/sources/policy-sl2-policy-returns-v1.md")
    assert context.source_note_sha256 == seed_document.processed_sha256
    assert hashlib.sha256(context.source_bytes).hexdigest() == seed_document.source_sha256
    assert context.source_note.provenance == seed_document.source_path
    assert context.note_text[context.body_start_char :] == context.body


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("document_family", "forged.returns-policy"),
        ("version_label", "forged-v1"),
        ("declared_effective_from", date(2024, 1, 16)),
        ("declared_effective_to", date(2027, 1, 1)),
        ("role", DocumentRole.FAQ),
        ("authority", DocumentAuthority.INFORMATIONAL),
    ),
)
def test_verified_context_rejects_fresh_metadata_not_in_exact_manifest_snapshot(
    field: str,
    value: object,
):
    manifest_context = _verified_manifest()
    original = next(
        item
        for item in manifest_context.manifest.documents
        if item.document_id == "sl2-policy-returns-v1"
    )
    payload = original.model_dump(mode="python")
    payload[field] = value
    forged = type(original).model_validate(payload)
    forged_documents = tuple(
        forged if item.document_id == original.document_id else item
        for item in manifest_context.manifest.documents
    )
    forged_manifest_context = replace(
        manifest_context,
        manifest=manifest_context.manifest.model_copy(update={"documents": forged_documents}),
    )

    with pytest.raises(SeedIntegrityError, match="parsed snapshot was altered"):
        verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=forged_manifest_context,
            document_id=original.document_id,
        )


def test_verified_context_rejects_false_source_hash_and_wrong_processed_hash(tmp_path: Path):
    document_id = "sl2-policy-returns-v1"
    source_data = _manifest_data()
    source_entry = next(
        item for item in source_data["documents"] if item["document_id"] == document_id
    )
    source_entry["source_sha256"] = "f" * 64
    with pytest.raises(SeedIntegrityError, match="hash drift"):
        verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=_write_manifest(tmp_path, source_data, name="source-drift.yaml"),
            document_id=document_id,
        )

    processed_data = _manifest_data()
    processed_entry = next(
        item for item in processed_data["documents"] if item["document_id"] == document_id
    )
    processed_entry["processed_sha256"] = "f" * 64
    with pytest.raises(SeedIntegrityError, match="hash drift"):
        verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=_write_manifest(
                tmp_path,
                processed_data,
                name="processed-drift.yaml",
            ),
            document_id=document_id,
        )


def test_verified_context_rejects_cross_document_path_even_with_matching_full_hash(
    tmp_path: Path,
):
    manifest = load_prechange_seed_manifest(MANIFEST)
    policy = next(
        item for item in manifest.documents if item.document_id == "sl2-policy-returns-v1"
    )
    faq = next(item for item in manifest.documents if item.document_id == "sl2-faq-returns")

    wrong_processed_data = _manifest_data()
    wrong_processed = next(
        item
        for item in wrong_processed_data["documents"]
        if item["document_id"] == policy.document_id
    )
    wrong_processed_faq = next(
        item for item in wrong_processed_data["documents"] if item["document_id"] == faq.document_id
    )
    wrong_processed["processed_path"] = faq.processed_path
    wrong_processed["processed_sha256"] = faq.processed_sha256
    wrong_processed_faq["processed_path"] = policy.processed_path
    wrong_processed_faq["processed_sha256"] = policy.processed_sha256
    with pytest.raises(SeedIntegrityError, match="exact source"):
        verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=_write_manifest(
                tmp_path,
                wrong_processed_data,
                name="wrong-processed.yaml",
            ),
            document_id=policy.document_id,
        )

    wrong_raw_data = _manifest_data()
    wrong_raw = next(
        item for item in wrong_raw_data["documents"] if item["document_id"] == policy.document_id
    )
    wrong_raw_faq = next(
        item for item in wrong_raw_data["documents"] if item["document_id"] == faq.document_id
    )
    wrong_raw["source_path"] = faq.source_path
    wrong_raw["source_sha256"] = faq.source_sha256
    wrong_raw_faq["source_path"] = policy.source_path
    wrong_raw_faq["source_sha256"] = policy.source_sha256
    with pytest.raises(SeedIntegrityError, match="exact source"):
        verify_seed_document_context(
            repo_root=REPO_ROOT,
            manifest_context=_write_manifest(
                tmp_path,
                wrong_raw_data,
                name="wrong-raw.yaml",
            ),
            document_id=policy.document_id,
        )


def test_verified_context_rejects_runtime_symlink_into_evaluator_gold(tmp_path: Path):
    seed_document = _document("sl2-policy-returns-v1")
    fake_repo = tmp_path / "repo"
    gold_dir = fake_repo / "datasets/larkstead/golden"
    gold_dir.mkdir(parents=True)
    gold_source = gold_dir / "policy-source.md"
    gold_source.write_bytes((REPO_ROOT / seed_document.source_path).read_bytes())

    source_link = fake_repo / seed_document.source_path
    source_link.parent.mkdir(parents=True, exist_ok=True)
    source_link.symlink_to(gold_source)
    processed = fake_repo / seed_document.processed_path
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes((REPO_ROOT / seed_document.processed_path).read_bytes())

    with pytest.raises(SeedBoundaryError, match="escapes its declared source root"):
        _verified_context(seed_document.document_id, repo_root=fake_repo)


def test_verified_context_uses_one_snapshot_and_detects_later_drift(tmp_path: Path):
    seed_document = _document("sl2-policy-returns-v1")
    fake_repo = _copy_binding(tmp_path, seed_document)
    manifest_context = _verified_manifest()
    context = _verified_context(
        seed_document.document_id,
        repo_root=fake_repo,
        manifest_context=manifest_context,
    )
    (fake_repo / seed_document.processed_path).write_bytes(b"drifted after verification")

    # The resolver uses the captured verified bytes, never a second filesystem read.
    revision = resolve_claim_revision(
        context=context,
        source_claim_id="policy-sl2-policy-returns-v1-01",
        declared_effective_from=seed_document.declared_effective_from,
        declared_effective_to=None,
        scopes=("return-window",),
    )
    assert (
        revision.statement == "Customers may return any item within 30 days of the delivery date."
    )

    with pytest.raises(SeedIntegrityError, match="hash drift"):
        _verified_context(
            seed_document.document_id,
            repo_root=fake_repo,
            manifest_context=manifest_context,
        )


def test_verified_context_rejects_duplicate_source_claim_ids(tmp_path: Path):
    seed_document = _document("sl2-policy-returns-v1")
    fake_repo = _copy_binding(tmp_path, seed_document)
    note_path = fake_repo / seed_document.processed_path
    text = note_path.read_text(encoding="utf-8")
    first_start = text.index("- id: policy-sl2-policy-returns-v1-01")
    second_start = text.index("- id:", first_start + 1)
    duplicate_block = text[first_start:second_start]
    mutated = text[:second_start] + duplicate_block + text[second_start:]
    note_path.write_text(mutated, encoding="utf-8")
    manifest_data = _manifest_data()
    manifest_entry = next(
        item
        for item in manifest_data["documents"]
        if item["document_id"] == seed_document.document_id
    )
    manifest_entry["processed_sha256"] = hashlib.sha256(mutated.encode("utf-8")).hexdigest()

    with pytest.raises(SeedIntegrityError, match="duplicate claim IDs"):
        verify_seed_document_context(
            repo_root=fake_repo,
            manifest_context=_write_manifest(
                tmp_path,
                manifest_data,
                name="duplicate-claims.yaml",
            ),
            document_id=seed_document.document_id,
        )


def test_resolvers_reject_a_tampered_verified_context():
    policy = _document("sl2-policy-returns-v1")
    faq = _document("sl2-faq-returns")
    context = _verified_context(policy.document_id)
    false_context = replace(context, document=faq.document_version())

    with pytest.raises(SeedIntegrityError, match="binding was altered"):
        resolve_claim_revision(
            context=false_context,
            source_claim_id="policy-sl2-policy-returns-v1-01",
            declared_effective_from=policy.declared_effective_from,
            declared_effective_to=None,
            scopes=("return-window",),
        )

    forged_seed = policy.model_copy(update={"document_family": "forged.returns-policy"})
    forged_context = replace(
        context,
        seed_document=forged_seed,
        document=forged_seed.document_version(),
    )
    with pytest.raises(SeedIntegrityError, match="exact manifest member"):
        resolve_claim_revision(
            context=forged_context,
            source_claim_id="policy-sl2-policy-returns-v1-01",
            declared_effective_from=policy.declared_effective_from,
            declared_effective_to=None,
            scopes=("return-window",),
        )


def test_claim_and_body_span_resolvers_use_derived_context_only():
    policy = _document("sl2-policy-returns-v1")
    policy_context = _verified_context(policy.document_id)
    revision = resolve_claim_revision(
        context=policy_context,
        source_claim_id="policy-sl2-policy-returns-v1-01",
        declared_effective_from=policy.declared_effective_from,
        declared_effective_to=None,
        scopes=("return-window",),
    )
    assert revision.document == policy_context.document
    assert revision.source.source_note_path == policy_context.source_note_path
    assert revision.source.source_note_sha256 == policy.processed_sha256
    assert (
        revision.statement == "Customers may return any item within 30 days of the delivery date."
    )

    with pytest.raises(SeedIntegrityError, match="resolve exactly once"):
        resolve_claim_revision(
            context=policy_context,
            source_claim_id="policy-sl2-policy-returns-v1-99",
            declared_effective_from=policy.declared_effective_from,
            declared_effective_to=None,
            scopes=("return-window",),
        )

    showroom = _document("process-showroom-demo-unit-rotation")
    showroom_context = _verified_context(showroom.document_id)
    quote = "Open-box sales carry the standard 30-day refund window like any order"
    start = showroom_context.note_text.index(quote, showroom_context.body_start_char)
    span = resolve_document_span(
        context=showroom_context,
        quote=quote,
        start_char=start,
    )
    assert span.quote == quote
    assert span.start_char == start
    assert span.source_note_path == showroom_context.source_note_path
    assert span.source_note_sha256 == showroom.processed_sha256

    with pytest.raises(SeedIntegrityError, match="file-relative offsets"):
        resolve_document_span(
            context=showroom_context,
            quote=quote,
            start_char=start + 1,
        )


def test_document_spans_reject_frontmatter_and_accept_body_file_offsets():
    showroom = _document("process-showroom-demo-unit-rotation")
    context = _verified_context(showroom.document_id)
    frontmatter_quote = "The showroom demo unit rotation is effective on 2025-07-01."
    frontmatter_start = context.note_text.index(frontmatter_quote)
    assert frontmatter_start < context.body_start_char
    with pytest.raises(SeedIntegrityError, match="canonical note body"):
        resolve_document_span(
            context=context,
            quote=frontmatter_quote,
            start_char=frontmatter_start,
        )

    body_quote = "Open-box sales carry the standard 30-day refund window like any order"
    body_start = context.note_text.index(body_quote, context.body_start_char)
    assert (
        resolve_document_span(
            context=context,
            quote=body_quote,
            start_char=body_start,
        ).start_char
        == body_start
    )


def test_materializer_creates_exact_prechange_context_without_mutating_shipped_corpus(
    tmp_path: Path,
):
    processed_v2 = (
        REPO_ROOT
        / "datasets/larkstead/processed/customer-support/sources/policy-sl2-policy-returns-v2.md"
    )
    v2_before = processed_v2.read_bytes()
    target = tmp_path / "prechange"

    report = materialize_prechange_seed(
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST,
        target=target,
    )
    assert report.document_count == 7
    assert report.reused is False
    assert processed_v2.read_bytes() == v2_before

    notes = sorted((target / "vault").rglob("*.md"))
    snapshots = sorted((target / "source_snapshot").rglob("*.md"))
    assert len(notes) == len(snapshots) == 7
    assert not any("returns-v2" in path.name for path in target.rglob("*"))
    all_note_text = "\n".join(path.read_text(encoding="utf-8") for path in notes)
    assert "policy-sl2-policy-returns-v2-01" not in all_note_text
    assert "You have 30 days from delivery" in all_note_text
    assert "outside our 30-day return window" in all_note_text
    assert "Open-box sales carry the standard 30-day refund window" in all_note_text
    assert "Returns per the 30-day window" in all_note_text

    receipt = json.loads((target / "change_control/seed-receipt.json").read_text())
    assert receipt["scenario_id"] == "sl2-returns-prechange"
    assert set(receipt["document_versions"]) == EXPECTED_DOCUMENT_IDS
    receipt_text = json.dumps(receipt)
    assert "expected_" not in receipt_text
    assert "patch" not in receipt_text
    assert "review" not in receipt_text


def test_materializer_preserves_and_verifies_exact_source_bytes(tmp_path: Path):
    target = tmp_path / "prechange"
    manifest = load_prechange_seed_manifest(MANIFEST)
    materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)

    for document in manifest.documents:
        source = REPO_ROOT / document.source_path
        copied = target / "source_snapshot" / document.source_path
        note = (
            target
            / "vault"
            / Path(document.processed_path).relative_to("datasets/larkstead/processed")
        )
        assert copied.read_bytes() == source.read_bytes()
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == document.source_sha256
        assert hashlib.sha256(note.read_bytes()).hexdigest() == document.processed_sha256


def test_materializer_is_idempotent_only_for_a_pristine_owned_target(tmp_path: Path):
    target = tmp_path / "prechange"
    first = materialize_prechange_seed(
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST,
        target=target,
    )
    second = materialize_prechange_seed(
        repo_root=REPO_ROOT,
        manifest_path=MANIFEST,
        target=target,
    )
    assert first.reused is False
    assert second.reused is True

    extra_directory = target / "unexpected-empty-directory"
    extra_directory.mkdir()
    with pytest.raises(SeedReuseError, match="missing, extra, or replaced"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)
    extra_directory.rmdir()

    (target / "index.db").write_bytes(b"not pristine")
    with pytest.raises(SeedReuseError, match="missing, extra, or replaced"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)


def test_materializer_pristine_reuse_rejects_special_files_without_opening_them(
    tmp_path: Path,
):
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform does not expose FIFO creation")
    target = tmp_path / "prechange"
    materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)
    os.mkfifo(target / "unexpected-pipe")

    with pytest.raises(SeedReuseError, match="missing, extra, or replaced"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)


@pytest.mark.parametrize(
    "target",
    (
        REPO_ROOT,
        REPO_ROOT / "datasets/larkstead/raw",
        REPO_ROOT / "datasets/larkstead/processed/would-be-disposable-seed",
        REPO_ROOT / "datasets/larkstead/golden/would-be-disposable-seed",
    ),
)
def test_materializer_rejects_every_target_at_or_below_repo_without_creating_files(
    target: Path,
):
    lock = target.parent / f".{target.name}.materialize.lock"
    target_existed = target.exists()
    lock_existed = lock.exists()
    with pytest.raises(SeedBoundaryError, match="outside the repository"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)
    assert target.exists() is target_existed
    assert lock.exists() is lock_existed


def test_materializer_rejects_external_symlink_route_into_repo_without_side_effects(
    tmp_path: Path,
):
    route = tmp_path / "into-repository"
    route.symlink_to(REPO_ROOT / "datasets/larkstead/processed", target_is_directory=True)
    target = route / "would-be-disposable-seed"
    actual_target = REPO_ROOT / "datasets/larkstead/processed/would-be-disposable-seed"
    actual_lock = actual_target.parent / f".{actual_target.name}.materialize.lock"
    assert not actual_target.exists()
    assert not actual_lock.exists()

    with pytest.raises(SeedBoundaryError, match="outside the repository"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)

    assert not actual_target.exists()
    assert not actual_lock.exists()


def test_materializer_rejects_case_alias_into_repo_without_side_effects():
    repo_text = REPO_ROOT.as_posix()
    if not repo_text.startswith("/Users/"):
        pytest.skip("case-alias probe is specific to the conventional macOS /Users path")
    alias_root = Path("/users") / Path(repo_text).relative_to("/Users")
    try:
        same_repository = alias_root.exists() and os.path.samefile(alias_root, REPO_ROOT)
    except OSError:
        same_repository = False
    if not same_repository:
        pytest.skip("filesystem does not expose a case-insensitive alias for this repository")

    name = ".m4-case-alias-would-be-seed"
    aliased_target = alias_root / name
    actual_target = REPO_ROOT / name
    actual_lock = REPO_ROOT / f".{name}.materialize.lock"
    assert not actual_target.exists()
    assert not actual_lock.exists()

    with pytest.raises(SeedBoundaryError, match="outside the repository"):
        materialize_prechange_seed(
            repo_root=REPO_ROOT,
            manifest_path=MANIFEST,
            target=aliased_target,
        )

    assert not actual_target.exists()
    assert not actual_lock.exists()


def test_materializer_never_deletes_a_foreign_lock(tmp_path: Path):
    target = tmp_path / "prechange"
    lock = tmp_path / ".prechange.materialize.lock"
    lock.write_bytes(b"another materializer owns this")

    with pytest.raises(SeedReuseError, match="another materializer"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)

    assert lock.read_bytes() == b"another materializer owns this"
    assert not target.exists()


def test_materializer_preserves_a_replacement_lock_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "prechange"
    lock = tmp_path / ".prechange.materialize.lock"

    def replace_lock_then_fail(_staging: Path, _files: object) -> None:
        lock.unlink()
        lock.write_bytes(b"replacement lock")
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(seed_module, "_publish_files", replace_lock_then_fail)
    with pytest.raises(RuntimeError, match="injected"):
        materialize_prechange_seed(repo_root=REPO_ROOT, manifest_path=MANIFEST, target=target)

    assert lock.read_bytes() == b"replacement lock"
    assert not target.exists()
    assert not list(tmp_path.glob(".prechange.staging-*"))


def test_materializer_rejects_missing_hash_drift_and_traversal(tmp_path: Path):
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    manifest_data = _manifest_data()
    path = tmp_path / "drift.yaml"
    path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(SeedIntegrityError, match="missing"):
        materialize_prechange_seed(
            repo_root=fake_repo,
            manifest_path=path,
            target=tmp_path / "missing-target",
        )

    manifest_data = _manifest_data()
    manifest_data["documents"][0]["source_sha256"] = "f" * 64
    path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(SeedIntegrityError, match="hash drift"):
        materialize_prechange_seed(
            repo_root=REPO_ROOT,
            manifest_path=path,
            target=tmp_path / "drift-target",
        )

    manifest_data = _manifest_data()
    manifest_data["documents"][0]["source_path"] = "../outside.md"
    path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError, match="safe repository-relative"):
        load_prechange_seed_manifest(path)
