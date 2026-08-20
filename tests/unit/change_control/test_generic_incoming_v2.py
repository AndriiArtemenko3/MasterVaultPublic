from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mastervault.change_control.generic_incoming import (
    GenericExtractionModeV2,
    GenericIncomingBoundaryError,
    GenericIncomingIntegrityError,
    admit_generic_incoming_markdown_v2,
    generic_extraction_prompt_variables_v2,
    ground_generic_extraction_v2,
    render_generic_source_note_v2,
)
from mastervault.change_control.generic_incoming_repository import (
    FilesystemGenericIncomingRepositoryV2,
    GenericIncomingRepositoryError,
)
from mastervault.config import PathsCfg
from mastervault.contracts.generic_grounded_claims import GenericGroundedClaimExtractionV2
from mastervault.prompts import registry


def _source(body: str, **updates: str) -> str:
    values = {
        "schema_version": "1",
        "event_id": "returns-event-v2",
        "document_id": "returns-policy-v2",
        "document_family": "returns-policy",
        "version_label": "v2",
        "title": "Returns Policy",
        "domain": "customer-support",
        "source_type": "policy",
        "declared_effective_from": "2026-08-20",
        "role": "policy",
        "authority": "primary",
        "operator_intent": "Admit this governing document.",
    }
    values.update(updates)
    fields = "\n".join(f"  {key}: {value}" for key, value in values.items())
    return f"---\nmastervault_change:\n{fields}\n---\n{body}"


def _write(tmp_path: Path, body: str, **updates: str) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700, parents=True)
    source = tmp_path / f"{updates.get('document_id', 'returns-policy-v2')}.md"
    source.write_text(_source(body, **updates), encoding="utf-8")
    source.chmod(0o600)
    return source, workspace


def _result(*quotes: str) -> dict[str, object]:
    return {
        "claims": [
            {"quote": quote, "confidence": "high", "affects": ["refund-policy"]}
            for quote in quotes
        ]
    }


def test_happy_path_unicode_offsets_determinism_and_exact_replay(tmp_path: Path) -> None:
    source, workspace = _write(
        tmp_path,
        "Café customers receive refunds.\nA résumé is retained for five days.\n",
    )
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    result = _result(
        "A résumé is retained for five days.",
        "Café customers receive refunds.",
    )
    live = ground_generic_extraction_v2(admission, result)
    assert [claim.claim_id for claim in live.claims] == [
        "returns-policy-v2-01",
        "returns-policy-v2-02",
    ]
    first = live.claims[0].evidence
    assert first.start_byte == first.start_char
    second = live.claims[1].evidence
    assert second.start_byte > second.start_char
    note = render_generic_source_note_v2(admission, live)
    assert note == render_generic_source_note_v2(admission, live)
    assert b"incoming/" in note and str(source).encode() not in note
    variables = generic_extraction_prompt_variables_v2(admission)
    assert variables["document"].count("<<<BEGIN UNTRUSTED") == 1
    prompt = registry.load("generic_grounded_claim_extraction_v2", version=2)
    assert prompt.output_model is GenericGroundedClaimExtractionV2
    assert "Café customers" in prompt.render(variables)

    replay = ground_generic_extraction_v2(
        admission, result, mode=GenericExtractionModeV2.REPLAY, replay_of=live
    )
    assert replay.claims == live.claims
    different, different_workspace = _write(
        tmp_path / "different",
        "Café customers receive refunds.\nA résumé is retained for six days.\n",
    )
    other = admit_generic_incoming_markdown_v2(different, active_workspace=different_workspace)
    with pytest.raises(GenericIncomingIntegrityError, match="content-bound LIVE"):
        ground_generic_extraction_v2(
            other, result, mode=GenericExtractionModeV2.REPLAY, replay_of=live
        )


def test_quote_rejects_ambiguity_paraphrase_fabrication_and_non_atomic(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\nReturns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    with pytest.raises(GenericIncomingIntegrityError, match="exactly once"):
        ground_generic_extraction_v2(admission, _result("Returns require a receipt."))
    for quote in ("A receipt is required.", "Invented policy."):
        with pytest.raises(GenericIncomingIntegrityError, match="exactly once"):
            ground_generic_extraction_v2(admission, _result(quote))
    unique, unique_workspace = _write(
        tmp_path / "atomic", "One sentence. Another sentence.\n"
    )
    admitted = admit_generic_incoming_markdown_v2(unique, active_workspace=unique_workspace)
    with pytest.raises(ValidationError, match="exactly one complete sentence"):
        ground_generic_extraction_v2(admitted, _result("One sentence. Another sentence."))


def test_only_body_is_evidence_and_natural_intent_words_are_allowed(tmp_path: Path) -> None:
    sentence = "Review impact before admission."
    source, workspace = _write(tmp_path, f"{sentence}\n", operator_intent=sentence)
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    grounded = ground_generic_extraction_v2(admission, _result(sentence))
    body_start = admission.source_text.index("\n---\n") + len("\n---\n")
    assert grounded.claims[0].evidence.start_char == body_start

    metadata_only, metadata_workspace = _write(
        tmp_path / "metadata-only",
        "A different governing sentence.\n",
        operator_intent=sentence,
    )
    admitted = admit_generic_incoming_markdown_v2(
        metadata_only, active_workspace=metadata_workspace
    )
    with pytest.raises(GenericIncomingIntegrityError, match="exactly once"):
        ground_generic_extraction_v2(admitted, _result(sentence))


@pytest.mark.parametrize(
    "operator_intent,match",
    [
        ("Use /private/tmp/answer.json.", "absolute path"),
        ("api_key=sk-not-a-real-key", "secret-shaped"),
        ("Use the expected impacts from evaluation.", "answer-shaped"),
    ],
)
def test_structured_metadata_rejects_path_secret_and_answer_leakage(
    tmp_path: Path, operator_intent: str, match: str
) -> None:
    source, workspace = _write(
        tmp_path,
        "A governing sentence.\n",
        operator_intent=operator_intent,
    )
    with pytest.raises(GenericIncomingBoundaryError, match=match):
        admit_generic_incoming_markdown_v2(source, active_workspace=workspace)


def test_claim_count_contract_accepts_one_and_ten_rejects_eleven() -> None:
    one = {"claims": [{"quote": "Claim one.", "confidence": "high", "affects": []}]}
    GenericGroundedClaimExtractionV2.model_validate_json(
        __import__("json").dumps(one)
    )
    ten = {"claims": one["claims"] * 10}
    GenericGroundedClaimExtractionV2.model_validate_json(__import__("json").dumps(ten))
    with pytest.raises(ValidationError):
        GenericGroundedClaimExtractionV2.model_validate_json(
            __import__("json").dumps({"claims": one["claims"] * 11})
        )


@pytest.mark.parametrize(
    "replacement,match",
    [
        ("  title: First\n  title: Second", "duplicate YAML key"),
        ("  title: &name First\n  operator_intent: *name", "aliases, anchors, or tags"),
        ("  title: !unsafe First", "safe YAML|aliases, anchors, or tags"),
        (
            "mastervault_change:\n  expected_impacts: refunds",
            "forbidden answer authority",
        ),
        ("outside: value\nmastervault_change:", "exactly one top-level"),
    ],
)
def test_strict_yaml_adversaries(tmp_path: Path, replacement: str, match: str) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    text = source.read_text(encoding="utf-8")
    text = text.replace("mastervault_change:", replacement, 1)
    source.write_text(text, encoding="utf-8")
    with pytest.raises(GenericIncomingBoundaryError, match=match):
        admit_generic_incoming_markdown_v2(source, active_workspace=workspace)


def test_source_type_size_mode_links_fifo_and_workspace_boundary(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    source.chmod(0o622)
    with pytest.raises(GenericIncomingBoundaryError, match="owner-controlled"):
        admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    source.chmod(0o600)
    hardlink = tmp_path / "hard.md"
    os.link(source, hardlink)
    with pytest.raises(GenericIncomingBoundaryError, match="owner-controlled"):
        admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    hardlink.unlink()
    symlink = tmp_path / "alias.md"
    symlink.symlink_to(source)
    with pytest.raises(GenericIncomingBoundaryError, match="symlink"):
        admit_generic_incoming_markdown_v2(symlink, active_workspace=workspace)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GenericIncomingIntegrityError, match="unavailable"):
        admit_generic_incoming_markdown_v2(
            linked_parent / source.name, active_workspace=workspace
        )
    fifo = tmp_path / "fifo.md"
    os.mkfifo(fifo)
    with pytest.raises(GenericIncomingBoundaryError, match="regular file"):
        admit_generic_incoming_markdown_v2(fifo, active_workspace=workspace)
    inside = workspace / "returns-policy-v2.md"
    inside.write_bytes(source.read_bytes())
    inside.chmod(0o600)
    with pytest.raises(GenericIncomingBoundaryError, match="outside"):
        admit_generic_incoming_markdown_v2(inside, active_workspace=workspace)
    source.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(GenericIncomingBoundaryError, match="65536-byte"):
        admit_generic_incoming_markdown_v2(source, active_workspace=workspace)


def test_admission_rejects_byte_and_inode_substitution(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    original = source.read_bytes()
    source.write_bytes(original.replace(b"receipt", b"invoice"))
    with pytest.raises(GenericIncomingIntegrityError, match="substituted or changed"):
        admission.verify_current_path()

    source.write_bytes(original)
    source.chmod(0o600)
    second = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    displaced = tmp_path / "displaced.md"
    source.rename(displaced)
    source.write_bytes(original)
    source.chmod(0o600)
    with pytest.raises(GenericIncomingIntegrityError, match="substituted or changed"):
        ground_generic_extraction_v2(second, _result("Returns require a receipt."))


def test_repository_lost_ack_reopen_tamper_identity_and_no_leakage(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    live = ground_generic_extraction_v2(admission, _result("Returns require a receipt."))
    root = tmp_path / "evidence"
    repository = FilesystemGenericIncomingRepositoryV2(root)
    first = repository.persist(admission, live)
    assert repository.persist(admission, live).bundle_id == first.bundle_id
    reopened = FilesystemGenericIncomingRepositoryV2(root, create=False).reopen(first.bundle_id)
    assert reopened.bundle_id == first.bundle_id
    persisted = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
    assert str(tmp_path).encode() not in persisted
    assert b"HMAC" not in persisted and b"_CAPABILITY_SECRET" not in persisted
    second_root = tmp_path / "second-evidence"
    assert (
        FilesystemGenericIncomingRepositoryV2(second_root).persist(admission, live).bundle_id
        == first.bundle_id
    )
    source_member = root / "generic-incoming" / "v2" / "sources" / f"{admission.source_sha256}.md"
    source_member.chmod(0o600)
    source_member.write_bytes(source_member.read_bytes() + b"tamper")
    with pytest.raises(GenericIncomingRepositoryError, match="source evidence bytes disagree"):
        repository.reopen(first.bundle_id)


def test_repository_rejects_non_private_member_mode(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    live = ground_generic_extraction_v2(admission, _result("Returns require a receipt."))
    root = tmp_path / "evidence"
    repository = FilesystemGenericIncomingRepositoryV2(root)
    capability = repository.persist(admission, live)
    bundles = root / "generic-incoming" / "v2" / "bundles"
    bundles.chmod(0o755)
    with pytest.raises(GenericIncomingRepositoryError, match="owner-only"):
        repository.reopen(capability.bundle_id)


def test_repository_replay_requires_recorded_live(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    result = _result("Returns require a receipt.")
    live = ground_generic_extraction_v2(admission, result)
    replay = ground_generic_extraction_v2(
        admission, result, mode=GenericExtractionModeV2.REPLAY, replay_of=live
    )
    repository = FilesystemGenericIncomingRepositoryV2(tmp_path / "evidence")
    with pytest.raises(GenericIncomingRepositoryError, match="already-recorded LIVE"):
        repository.persist(admission, replay)
    repository.persist(admission, live)
    assert repository.persist(admission, replay).bundle_id


def test_receipt_identity_binds_exact_raw_bytes(tmp_path: Path) -> None:
    source, workspace = _write(tmp_path, "Returns require a receipt.\n")
    admission = admit_generic_incoming_markdown_v2(source, active_workspace=workspace)
    assert admission.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert PathsCfg(workspace=workspace).change_control_evidence_root == (
        workspace / "change_control" / "evidence"
    )
