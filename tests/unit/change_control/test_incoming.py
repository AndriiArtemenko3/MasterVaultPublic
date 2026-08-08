from __future__ import annotations

import hashlib
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import mastervault.change_control.incoming as incoming_module
from mastervault.change_control.incoming import (
    ALIGNMENT_ATTESTATION_ID,
    ALIGNMENT_ATTESTATION_RELATIVE_PATH,
    ALIGNMENT_POLICY_VERSION,
    DOCUMENT_ID,
    EVENT_ID,
    MANIFEST_RELATIVE_PATH,
    MAX_ALIGNMENT_ATTESTATION_BYTES,
    MAX_INCOMING_CLAIMS,
    MAX_PROCESSED_NOTE_BYTES,
    MAX_RAW_EVIDENCE_BYTES,
    MAX_SCAN_DEPTH,
    MAX_SCAN_NODES,
    MAX_SOURCE_BYTES,
    PINNED_ALIGNMENT_ATTESTATION_SHA256,
    PROCESSED_RELATIVE_PATH,
    SOURCE_RELATIVE_PATH,
    IncomingBoundaryError,
    IncomingIntegrityError,
    load_verified_incoming_event,
)
from mastervault.change_control.models import ComparableClaimPair
from mastervault.change_control.seed import (
    load_verified_prechange_seed_manifest,
    resolve_claim_revision,
    verify_seed_document_context,
)
from mastervault.models import content_hash
from mastervault.vaultfs.frontmatter import parse_frontmatter, serialize_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / MANIFEST_RELATIVE_PATH
ALIGNMENT_ATTESTATION = REPO_ROOT / ALIGNMENT_ATTESTATION_RELATIVE_PATH
PRECHANGE_MANIFEST = REPO_ROOT / "datasets/larkstead/change_control/sl2_prechange.yaml"


def _manifest_data(root: Path = REPO_ROOT) -> dict:
    data = yaml.safe_load((root / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _attestation_data(root: Path = REPO_ROOT) -> dict:
    data = yaml.safe_load((root / ALIGNMENT_ATTESTATION_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _copy_runtime_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in (
        MANIFEST_RELATIVE_PATH,
        ALIGNMENT_ATTESTATION_RELATIVE_PATH,
        SOURCE_RELATIVE_PATH,
        PROCESSED_RELATIVE_PATH,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return root


def _write_manifest(root: Path, data: dict) -> None:
    (root / MANIFEST_RELATIVE_PATH).write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def _write_attestation(root: Path, data: dict) -> None:
    (root / ALIGNMENT_ATTESTATION_RELATIVE_PATH).write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def _rehash_manifest_file(root: Path, key: str, relative: str) -> None:
    data = _manifest_data(root)
    data["document"][key] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    _write_manifest(root, data)


def _load(root: Path = REPO_ROOT):
    return load_verified_incoming_event(
        repo_root=root,
        manifest_path=root / MANIFEST_RELATIVE_PATH,
    )


def _rewrite_note(root: Path, mutate) -> None:
    path = root / PROCESSED_RELATIVE_PATH
    data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    mutate(data, body)
    path.write_text(f"---\n{serialize_frontmatter(data)}---\n{body}", encoding="utf-8")
    _rehash_manifest_file(root, "processed_sha256", PROCESSED_RELATIVE_PATH)


def _rewrite_raw_and_note_content(root: Path, raw_text: str) -> None:
    raw_path = root / SOURCE_RELATIVE_PATH
    raw_path.write_text(raw_text, encoding="utf-8")
    note_path = root / PROCESSED_RELATIVE_PATH
    data, body = parse_frontmatter(note_path.read_text(encoding="utf-8"))
    data["provenance_hash"] = content_hash(raw_text)
    before, marker, _old_content = body.partition("\n## Content\n\n")
    assert marker
    rendered_content = raw_text if raw_text.endswith("\n") else f"{raw_text}\n"
    note_path.write_text(
        f"---\n{serialize_frontmatter(data)}---\n{before}{marker}{rendered_content}",
        encoding="utf-8",
    )
    manifest = _manifest_data(root)
    manifest["document"]["source_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest["document"]["processed_sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
    _write_manifest(root, manifest)


def test_verified_event_binds_exact_raw_evidence_document_and_claims() -> None:
    first = _load()
    second = _load()

    assert first.manifest.event_id == EVENT_ID
    assert first.document.document_id == DOCUMENT_ID
    assert first.manifest.event_id != first.document.document_id
    assert first.alignment_attestation_id == ALIGNMENT_ATTESTATION_ID
    assert first.alignment_policy_version == ALIGNMENT_POLICY_VERSION
    assert first.claim_scope_policy_version == "claim-scopes-v1"
    assert first.alignment_attestation_sha256 == PINNED_ALIGNMENT_ATTESTATION_SHA256
    assert (
        first.alignment_attestation_sha256
        == hashlib.sha256(ALIGNMENT_ATTESTATION.read_bytes()).hexdigest()
    )
    assert first.alignment_payload_sha256 == _attestation_data()["payload_sha256"]
    assert first.document.source_sha256 == hashlib.sha256(first.source_snapshot).hexdigest()
    assert (
        first.manifest.document.processed_sha256
        == hashlib.sha256(first.processed_snapshot).hexdigest()
    )
    assert len(first.grounded_claims) == len(first.claim_revisions) == MAX_INCOMING_CLAIMS
    assert first.aggregate_claim_roots == first.claim_revisions
    assert {item.revision.source.source_claim_id for item in first.grounded_claims} == {
        f"policy-sl2-policy-returns-v2-{ordinal:02d}" for ordinal in range(1, 11)
    }
    for grounded in first.grounded_claims:
        assert len(grounded.raw_evidence) == 1
        for span in grounded.raw_evidence:
            assert first.source_snapshot[span.start_byte : span.end_byte] == span.quote.encode()
        assert hashlib.sha256(grounded.revision.statement.encode()).hexdigest() == (
            grounded.extractive_statement_sha256
        )
        assert grounded.revision.statement == " ".join(grounded.raw_evidence[0].quote.split())
        assert grounded.revision.source.evidence == ()
    assert first.event_identity == second.event_identity
    assert first.event_identity.startswith("incoming:")
    assert len(first.event_identity) == len("incoming:") + 64


def test_scope_policy_derives_only_from_family_and_claim_affects() -> None:
    event = _load()
    note_data, _body = parse_frontmatter(event.processed_snapshot.decode())
    claims_by_id = {claim["id"]: claim for claim in note_data["key_claims"]}
    for revision in event.claim_revisions:
        source_claim = claims_by_id[revision.source.source_claim_id]
        assert revision.scopes == tuple(
            sorted({event.document.document_family, *source_claim["affects"]})
        )
    assert "scopes" not in yaml.safe_dump(event.manifest.model_dump(mode="json"))


def test_v1_and_v2_return_window_claims_remain_comparable() -> None:
    manifest = load_verified_prechange_seed_manifest(PRECHANGE_MANIFEST)
    context = verify_seed_document_context(
        repo_root=REPO_ROOT,
        manifest_context=manifest,
        document_id="sl2-policy-returns-v1",
    )
    v1 = resolve_claim_revision(
        context=context,
        source_claim_id="policy-sl2-policy-returns-v1-01",
        declared_effective_from=date(2024, 1, 15),
        declared_effective_to=None,
        scopes=("customer-support.returns-policy", "return-policy"),
    )
    v2 = next(
        item for item in _load().claim_revisions if item.source.source_claim_id.endswith("-01")
    )
    pair = ComparableClaimPair.create(v1, v2)
    assert pair.shared_scopes == (
        "customer-support.returns-policy",
        "return-policy",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_impacts", []),
        ("classification", "SUPERSEDES"),
        ("patches", [{"before": "30", "after": "45"}]),
        ("expected_review_decision", "approve"),
    ],
)
def test_manifest_rejects_recursive_evaluator_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["document"]["nested"] = {field: value}
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="evaluator field"):
        _load(root)


def test_manifest_rejects_evaluator_labels_before_schema_validation(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["operator"] = {"disposition": "CONTRADICTS"}
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="evaluator label"):
        _load(root)


@pytest.mark.parametrize("location", ["top", "claim"])
def test_processed_note_rejects_nested_evaluator_fields(tmp_path: Path, location: str) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        target = data if location == "top" else data["key_claims"][0]
        target["expected_impacts" if location == "top" else "patches"] = []

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingBoundaryError, match="evaluator field"):
        _load(root)


@pytest.mark.parametrize("location", ["top", "claim"])
def test_processed_note_schema_is_strict_against_benign_unknown_fields(
    tmp_path: Path, location: str
) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        target = data if location == "top" else data["key_claims"][0]
        target["operator_note"] = "ordinary metadata"

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingIntegrityError, match="unknown fields"):
        _load(root)


def test_processed_note_body_preamble_rejects_answer_shaped_text_after_rehash(
    tmp_path: Path,
) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / PROCESSED_RELATIVE_PATH
    text = path.read_text(encoding="utf-8").replace(
        "\n## Content\n\n",
        "\nExpected impacts: approve a downstream patch.\n\n## Content\n\n",
        1,
    )
    path.write_text(text, encoding="utf-8")
    _rehash_manifest_file(root, "processed_sha256", PROCESSED_RELATIVE_PATH)
    with pytest.raises(IncomingBoundaryError, match="evaluator-shaped text"):
        _load(root)


def test_manifest_scanner_rejects_cyclic_yaml_alias(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / MANIFEST_RELATIVE_PATH
    path.write_text(
        path.read_text(encoding="utf-8") + "\ncycle: &cycle\n  self: *cycle\n",
        encoding="utf-8",
    )
    with pytest.raises(IncomingBoundaryError, match="anchors or aliases"):
        _load(root)


@pytest.mark.parametrize("syntax", ["duplicate", "alias"])
def test_manifest_rejects_duplicate_keys_and_all_yaml_aliases(tmp_path: Path, syntax: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / MANIFEST_RELATIVE_PATH
    if syntax == "duplicate":
        path.write_text(
            path.read_text(encoding="utf-8") + "\nschema_version: 1\n",
            encoding="utf-8",
        )
        match = "duplicate YAML key"
    else:
        path.write_text(
            path.read_text(encoding="utf-8") + "\nmetadata: &shared [one]\ncopy: *shared\n",
            encoding="utf-8",
        )
        match = "anchors or aliases"
    with pytest.raises(IncomingBoundaryError, match=match):
        _load(root)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("operator_note", "ordinary metadata", "attestation is invalid"),
        ("expected_impacts", [], "evaluator field"),
        ("alignment_policy_version", "other-policy-v1", "attestation is invalid"),
    ],
)
def test_alignment_attestation_has_strict_policy_and_evaluator_schema(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _attestation_data(root)
    data[field] = value
    _write_attestation(root, data)
    with pytest.raises(IncomingBoundaryError, match=match):
        _load(root)


def test_alignment_attestation_rejects_all_yaml_aliases(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / ALIGNMENT_ATTESTATION_RELATIVE_PATH
    path.write_text(
        path.read_text(encoding="utf-8") + "\nmetadata: &shared [one]\ncopy: *shared\n",
        encoding="utf-8",
    )
    with pytest.raises(IncomingBoundaryError, match="anchors or aliases"):
        _load(root)


def test_self_consistent_but_unpinned_attestation_file_fails(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / ALIGNMENT_ATTESTATION_RELATIVE_PATH
    path.write_text(
        path.read_text(encoding="utf-8") + "# Same reviewed payload, different file bytes.\n",
        encoding="utf-8",
    )
    with pytest.raises(IncomingIntegrityError, match="code-pinned fixture"):
        _load(root)


@pytest.mark.parametrize("syntax", ["duplicate", "alias"])
def test_processed_note_rejects_duplicate_keys_and_all_yaml_aliases(
    tmp_path: Path, syntax: str
) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / PROCESSED_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    if syntax == "duplicate":
        text = text.replace(
            "title: Sl2 Policy Returns V2\n",
            "title: evaluator answer hidden by duplicate\ntitle: Sl2 Policy Returns V2\n",
            1,
        )
        match = "duplicate YAML key"
    else:
        text = text.replace("tags:\n", "tags: &shared\n", 1).replace(
            "status: processed\n", "status: processed\nalias_copy: *shared\n", 1
        )
        match = "anchors or aliases"
    path.write_text(text, encoding="utf-8")
    _rehash_manifest_file(root, "processed_sha256", PROCESSED_RELATIVE_PATH)
    with pytest.raises(IncomingBoundaryError, match=match):
        _load(root)


def test_manifest_scanner_rejects_excessive_nesting(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    nested: dict = {}
    cursor = nested
    for index in range(MAX_SCAN_DEPTH + 2):
        child: dict = {}
        cursor[f"level_{index}"] = child
        cursor = child
    data["metadata"] = nested
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="nesting limit"):
        _load(root)


def test_manifest_scanner_rejects_excessive_nodes(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["metadata"] = [0] * (MAX_SCAN_NODES + 1)
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="node scan limit"):
        _load(root)


def test_document_identity_must_match_event_files(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["document"]["document_id"] = "incoming-document"
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="manifest is invalid"):
        _load(root)


def test_reviewed_attestation_binds_family_and_version(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["document"]["document_family"] = "customer-support.alternate-policy"
    data["document"]["version_label"] = "revision-2026"
    _write_manifest(root, data)
    with pytest.raises(IncomingIntegrityError, match="reviewed attestation"):
        _load(root)


def test_event_id_is_the_exact_logical_event_identity(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["event_id"] = data["document"]["document_id"]
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="manifest is invalid"):
        _load(root)


def test_event_and_document_identities_remain_distinct_after_coherent_rename(
    tmp_path: Path,
) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    event_id = data["event_id"]
    source_relative = f"datasets/larkstead/raw/customer-support/policy/{event_id}.md"
    processed_relative = (
        f"datasets/larkstead/processed/customer-support/sources/policy-{event_id}.md"
    )
    (root / SOURCE_RELATIVE_PATH).rename(root / source_relative)
    (root / PROCESSED_RELATIVE_PATH).rename(root / processed_relative)
    data["document"]["document_id"] = event_id
    data["document"]["source_path"] = source_relative
    data["document"]["processed_path"] = processed_relative
    _write_manifest(root, data)
    with pytest.raises(IncomingBoundaryError, match="identities must remain distinct"):
        _load(root)


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("source_path", "datasets/larkstead/raw/../golden/change_impact.yaml"),
        ("processed_path", "datasets/larkstead/processed/GOLDEN/answers.md"),
        ("source_path", "/tmp/sl2-policy-returns-v2.md"),
        ("source_path", f" {SOURCE_RELATIVE_PATH}"),
        ("source_path", SOURCE_RELATIVE_PATH.replace("/", "\\")),
        (
            "source_path",
            "datasets/larkstead/raw/.golden/sl2-policy-returns-v2.md",
        ),
    ],
)
def test_manifest_rejects_unsafe_paths(tmp_path: Path, field: str, path: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    data["document"][field] = path
    _write_manifest(root, data)
    with pytest.raises(
        IncomingBoundaryError,
        match="manifest is invalid|evaluator gold|hidden components|evaluator-shaped text",
    ):
        _load(root)


def test_loader_refuses_manifest_outside_exact_runtime_path(tmp_path: Path) -> None:
    alternate = tmp_path / "golden" / "incoming.yaml"
    alternate.parent.mkdir()
    alternate.write_bytes(MANIFEST.read_bytes())
    with pytest.raises(IncomingBoundaryError, match="evaluator-gold"):
        load_verified_incoming_event(repo_root=REPO_ROOT, manifest_path=alternate)


@pytest.mark.parametrize(
    "relative",
    [
        MANIFEST_RELATIVE_PATH,
        ALIGNMENT_ATTESTATION_RELATIVE_PATH,
        SOURCE_RELATIVE_PATH,
        PROCESSED_RELATIVE_PATH,
    ],
)
def test_loader_rejects_symlinked_runtime_inputs(tmp_path: Path, relative: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    path = root / relative
    original = tmp_path / f"original-{path.name}"
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises((IncomingBoundaryError, IncomingIntegrityError), match="symlink|regular"):
        _load(root)


def test_alignment_attestation_is_required_at_exact_allowlisted_path(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    exact = root / ALIGNMENT_ATTESTATION_RELATIVE_PATH
    alternate = exact.with_name("alternate.yaml")
    exact.rename(alternate)
    with pytest.raises(IncomingIntegrityError, match="disappeared|unavailable"):
        _load(root)


def test_alignment_attestation_has_a_fixed_size_limit(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    (root / ALIGNMENT_ATTESTATION_RELATIVE_PATH).write_bytes(
        b"x" * (MAX_ALIGNMENT_ATTESTATION_BYTES + 1)
    )
    with pytest.raises(IncomingIntegrityError, match="fixed .*byte limit"):
        _load(root)


def test_alignment_attestation_read_detects_same_file_metadata_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "alignment.yaml"
    target.write_bytes(ALIGNMENT_ATTESTATION.read_bytes())
    original_lstat = Path.lstat
    calls = 0

    def changed_metadata_lstat(path: Path):
        nonlocal calls
        info = original_lstat(path)
        if path == target:
            calls += 1
            if calls == 2:
                values = {
                    name: getattr(info, name)
                    for name in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                values["st_ctime_ns"] += 1
                return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(Path, "lstat", changed_metadata_lstat)
    with pytest.raises(IncomingIntegrityError, match="changed during"):
        incoming_module._read_regular(
            target,
            limit=MAX_ALIGNMENT_ATTESTATION_BYTES,
            label="incoming alignment attestation",
        )


def test_loader_converts_post_read_disappearance_to_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "source.md"
    target.write_text("source", encoding="utf-8")
    original_lstat = Path.lstat
    calls = 0

    def disappearing_lstat(path: Path):
        nonlocal calls
        if path == target:
            calls += 1
            if calls == 2:
                path.unlink()
                raise FileNotFoundError(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", disappearing_lstat)
    with pytest.raises(IncomingIntegrityError, match="disappeared after"):
        incoming_module._read_regular(target, limit=1024, label="test source")


def test_loader_converts_post_read_symlink_swap_to_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "source.md"
    replacement = tmp_path / "replacement.md"
    target.write_text("source", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    original_lstat = Path.lstat
    calls = 0

    def swapping_lstat(path: Path):
        nonlocal calls
        if path == target:
            calls += 1
            if calls == 2:
                path.unlink()
                path.symlink_to(replacement)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    with pytest.raises(IncomingIntegrityError, match="changed during"):
        incoming_module._read_regular(target, limit=1024, label="test source")


def test_loader_rejects_same_inode_same_size_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "source.md"
    target.write_text("source", encoding="utf-8")
    original_lstat = Path.lstat
    calls = 0

    def changed_metadata_lstat(path: Path):
        nonlocal calls
        info = original_lstat(path)
        if path == target:
            calls += 1
            if calls == 2:
                values = {
                    name: getattr(info, name)
                    for name in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                values["st_mtime_ns"] += 1
                return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(Path, "lstat", changed_metadata_lstat)
    with pytest.raises(IncomingIntegrityError, match="changed during"):
        incoming_module._read_regular(target, limit=1024, label="test source")


def test_loader_converts_missing_source_resolve_race_to_typed_error(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    (root / SOURCE_RELATIVE_PATH).unlink()
    with pytest.raises(IncomingIntegrityError, match="disappeared"):
        _load(root)


def test_loader_rejects_raw_and_processed_hash_drift(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    (root / SOURCE_RELATIVE_PATH).write_text("tampered", encoding="utf-8")
    with pytest.raises(IncomingIntegrityError, match="raw source SHA-256"):
        _load(root)

    root = _copy_runtime_repo(tmp_path / "second")
    with (root / PROCESSED_RELATIVE_PATH).open("a", encoding="utf-8") as handle:
        handle.write("\ntampered\n")
    with pytest.raises(IncomingIntegrityError, match="canonical note SHA-256"):
        _load(root)


def test_claim_statement_drift_fails_even_when_note_hash_is_recomputed(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        data["key_claims"][0]["statement"] = (
            "Customers may return any item within 46 days of the delivery date."
        )

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingIntegrityError, match="statement drifted"):
        _load(root)


def test_rehashed_answer_shaped_processed_claim_is_rejected_as_evaluator_data(
    tmp_path: Path,
) -> None:
    root = _copy_runtime_repo(tmp_path)
    malicious = "The evaluator says the correct downstream decision is approve."

    def mutate(data: dict, _body: str) -> None:
        data["key_claims"][0]["statement"] = malicious

    _rewrite_note(root, mutate)
    manifest = _manifest_data(root)
    manifest["document"]["claim_bindings"][0]["statement_sha256"] = hashlib.sha256(
        malicious.encode()
    ).hexdigest()
    _write_manifest(root, manifest)

    with pytest.raises(IncomingBoundaryError, match="evaluator-shaped text"):
        _load(root)


@pytest.mark.parametrize(
    ("claim_index", "malicious"),
    [
        (
            9,
            "Defective items are refunded or replaced at charge, and Larkstead covers return shipping.",
        ),
        (
            8,
            "The restocking fee is waived on B2B orders of 2025 or more units, per the 10-06-02 update.",
        ),
        (
            9,
            "Larkstead is refunded or replaced at no charge, and defective items cover return shipping.",
        ),
    ],
)
def test_rehashed_processed_semantics_fail_reviewed_alignment(
    tmp_path: Path,
    claim_index: int,
    malicious: str,
) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        data["key_claims"][claim_index]["statement"] = malicious

    _rewrite_note(root, mutate)
    manifest = _manifest_data(root)
    binding = manifest["document"]["claim_bindings"][claim_index]
    binding["statement_sha256"] = hashlib.sha256(malicious.encode()).hexdigest()
    _write_manifest(root, manifest)

    with pytest.raises(IncomingIntegrityError, match="reviewed attestation"):
        _load(root)


def test_noncanonical_full_width_processed_statement_fails_closed(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    full_width = "Ｃustomers may return any item within 45 days of the delivery date."

    def mutate(data: dict, _body: str) -> None:
        data["key_claims"][0]["statement"] = full_width

    _rewrite_note(root, mutate)
    manifest = _manifest_data(root)
    manifest["document"]["claim_bindings"][0]["statement_sha256"] = hashlib.sha256(
        full_width.encode()
    ).hexdigest()
    _write_manifest(root, manifest)
    with pytest.raises(IncomingIntegrityError, match="statement is not canonical"):
        _load(root)


@pytest.mark.parametrize("mode", ["swap", "permutation"])
def test_reviewed_alignment_rejects_valid_raw_span_reassignment(tmp_path: Path, mode: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    bindings = data["document"]["claim_bindings"]
    if mode == "swap":
        bindings[0]["evidence"], bindings[1]["evidence"] = (
            bindings[1]["evidence"],
            bindings[0]["evidence"],
        )
    else:
        spans = [binding["evidence"] for binding in bindings]
        for index, binding in enumerate(bindings):
            binding["evidence"] = spans[(index + 1) % len(spans)]
    _write_manifest(root, data)
    with pytest.raises(IncomingIntegrityError, match="reviewed attestation"):
        _load(root)


def test_reviewed_alignment_rejects_valid_affects_tamper(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        data["key_claims"][0]["affects"] = ["refund-policy", "return-policy"]

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingIntegrityError, match="reviewed attestation"):
        _load(root)


@pytest.mark.parametrize("mode", ["duplicate", "unordered", "too_many", "too_long"])
def test_claim_affects_are_bounded_canonical_unique_annotations(tmp_path: Path, mode: str) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        if mode == "duplicate":
            affects = ["return-policy", "return-policy"]
        elif mode == "unordered":
            affects = ["return-policy", "refund-policy"]
        elif mode == "too_many":
            affects = [f"scope-{index:02d}" for index in range(17)]
        else:
            affects = ["a" * 129]
        data["key_claims"][0]["affects"] = affects

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingIntegrityError, match="affects"):
        _load(root)


@pytest.mark.parametrize("field", ["start_char", "start_byte", "quote"])
def test_claim_raw_evidence_quote_and_offsets_are_exact(tmp_path: Path, field: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    evidence = data["document"]["claim_bindings"][0]["evidence"][0]
    if field == "quote":
        evidence[field] = evidence[field].replace("45", "46")
    else:
        evidence[field] += 1
        evidence["end_char" if field == "start_char" else "end_byte"] += 1
    _write_manifest(root, data)
    with pytest.raises(IncomingIntegrityError, match="evidence quote/span|offsets disagree"):
        _load(root)


@pytest.mark.parametrize("mode", ["full_source", "two_sentences", "duplicate_span"])
def test_claim_raw_evidence_is_atomic_bounded_and_unique(tmp_path: Path, mode: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    bindings = data["document"]["claim_bindings"]
    if mode == "full_source":
        source = (root / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")
        assert len(source.encode()) > MAX_RAW_EVIDENCE_BYTES
        bindings[0]["evidence"] = [
            {
                "quote": source,
                "start_char": 0,
                "end_char": len(source),
                "start_byte": 0,
                "end_byte": len(source.encode()),
            }
        ]
    elif mode == "two_sentences":
        source = (root / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")
        quote = source[234:373]
        bindings[0]["evidence"] = [
            {
                "quote": quote,
                "start_char": 234,
                "end_char": 373,
                "start_byte": 234,
                "end_byte": 373,
            }
        ]
    else:
        bindings[1]["evidence"] = [dict(bindings[0]["evidence"][0])]
    _write_manifest(root, data)
    with pytest.raises(
        IncomingBoundaryError,
        match="manifest is invalid|atomic-span limit|complete sentence|unique atomic raw span",
    ):
        _load(root)


def test_raw_change_fails_evidence_after_all_file_hashes_are_recomputed(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    raw = (root / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")
    _rewrite_raw_and_note_content(
        root,
        raw.replace("within 45 days of the delivery date", "within 46 days of the delivery date"),
    )
    with pytest.raises(IncomingIntegrityError, match="evidence quote/span"):
        _load(root)


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_claim_bindings_must_cover_each_claim_exactly_once(tmp_path: Path, mode: str) -> None:
    root = _copy_runtime_repo(tmp_path)
    data = _manifest_data(root)
    bindings = data["document"]["claim_bindings"]
    if mode == "missing":
        bindings.pop()
    else:
        bindings[1]["source_claim_id"] = bindings[0]["source_claim_id"]
    _write_manifest(root, data)
    error = IncomingIntegrityError if mode == "missing" else IncomingBoundaryError
    with pytest.raises(error, match="exactly cover|manifest is invalid"):
        _load(root)


def test_content_comparison_canonicalizes_only_one_optional_terminal_lf(
    tmp_path: Path,
) -> None:
    no_lf_root = _copy_runtime_repo(tmp_path / "no-lf")
    raw = (no_lf_root / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    _rewrite_raw_and_note_content(no_lf_root, raw[:-1])
    with pytest.raises(IncomingIntegrityError, match="reviewed attestation"):
        _load(no_lf_root)

    extra_lf_root = _copy_runtime_repo(tmp_path / "extra-lf")
    raw = (extra_lf_root / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")
    raw_path = extra_lf_root / SOURCE_RELATIVE_PATH
    raw_path.write_text(f"{raw}\n", encoding="utf-8")
    note_path = extra_lf_root / PROCESSED_RELATIVE_PATH
    data, body = parse_frontmatter(note_path.read_text(encoding="utf-8"))
    data["provenance_hash"] = content_hash(f"{raw}\n")
    note_path.write_text(f"---\n{serialize_frontmatter(data)}---\n{body}", encoding="utf-8")
    manifest = _manifest_data(extra_lf_root)
    manifest["document"]["source_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest["document"]["processed_sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
    _write_manifest(extra_lf_root, manifest)
    with pytest.raises(IncomingIntegrityError, match="differs beyond one optional terminal LF"):
        _load(extra_lf_root)


def test_provenance_mismatch_fails_even_when_note_hash_is_updated(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        data["provenance"] = "datasets/larkstead/raw/customer-support/policy/other.md"

    _rewrite_note(root, mutate)
    with pytest.raises(IncomingIntegrityError, match="provenance does not name"):
        _load(root)


@pytest.mark.parametrize("duplicate", ["id", "statement"])
def test_loader_rejects_duplicate_claims(tmp_path: Path, duplicate: str) -> None:
    root = _copy_runtime_repo(tmp_path)

    def mutate(data: dict, _body: str) -> None:
        claims = data["key_claims"]
        claims[1][duplicate] = claims[0][duplicate]

    _rewrite_note(root, mutate)
    with pytest.raises(
        IncomingIntegrityError,
        match=f"duplicate claim {'IDs' if duplicate == 'id' else 'statements'}",
    ):
        _load(root)


def test_loader_enforces_fixed_source_note_and_claim_limits(tmp_path: Path) -> None:
    source_root = _copy_runtime_repo(tmp_path / "source")
    (source_root / SOURCE_RELATIVE_PATH).write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    _rehash_manifest_file(source_root, "source_sha256", SOURCE_RELATIVE_PATH)
    with pytest.raises(IncomingIntegrityError, match="fixed .*byte limit"):
        _load(source_root)

    note_root = _copy_runtime_repo(tmp_path / "note")
    (note_root / PROCESSED_RELATIVE_PATH).write_bytes(b"x" * (MAX_PROCESSED_NOTE_BYTES + 1))
    _rehash_manifest_file(note_root, "processed_sha256", PROCESSED_RELATIVE_PATH)
    with pytest.raises(IncomingIntegrityError, match="fixed .*byte limit"):
        _load(note_root)

    claim_root = _copy_runtime_repo(tmp_path / "claims")

    def add_claim(data: dict, _body: str) -> None:
        extra = dict(data["key_claims"][-1])
        extra["affects"] = list(extra["affects"])
        extra["id"] = "policy-sl2-policy-returns-v2-11"
        extra["statement"] = "A distinct eleventh claim exists only to test the fixed limit."
        data["key_claims"].append(extra)

    _rewrite_note(claim_root, add_claim)
    with pytest.raises(IncomingIntegrityError, match="fixed 10-claim limit"):
        _load(claim_root)


@pytest.mark.parametrize(
    "field",
    ["_source_snapshot", "_grounded_claims", "_manifest", "_attestation_snapshot"],
)
def test_capability_seal_detects_in_memory_tampering(field: str) -> None:
    context = _load()
    if field in {"_source_snapshot", "_attestation_snapshot"}:
        object.__setattr__(context, field, b"tampered")
    elif field == "_grounded_claims":
        object.__setattr__(context, field, ())
    else:
        object.__setattr__(context._manifest.document, "document_id", "counterfactual-document")
    with pytest.raises(IncomingIntegrityError, match="altered"):
        _ = context.document


def test_tracked_raw_and_source_note_fixture_hashes_remain_unchanged() -> None:
    assert hashlib.sha256((REPO_ROOT / SOURCE_RELATIVE_PATH).read_bytes()).hexdigest() == (
        "d9ee838873469234faa0e5e435ab61cfda1b1cff32daf7d792f7b1e8d673bb6d"
    )
    assert hashlib.sha256((REPO_ROOT / PROCESSED_RELATIVE_PATH).read_bytes()).hexdigest() == (
        "8788c42e9d74a47ba44d9d37cd6557751994220c9c9596ff02da4d084dc89bd1"
    )


def test_loading_is_read_only(tmp_path: Path) -> None:
    root = _copy_runtime_repo(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    _load(root)
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_module_does_not_import_evaluator_or_seed_implementation() -> None:
    source = Path(incoming_module.__file__).read_text(encoding="utf-8")
    assert "change_impact" not in source
    assert "datasets/larkstead/golden" not in source.casefold()
    assert "change_control.golden" not in source
    assert "change_control.seed" not in source
