from __future__ import annotations

import hashlib
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from mastervault.change_control.managed_review import (
    GenericManagedAnalysisSetBindingV3,
    ManagedAnalysisSetBinding,
    ManagedArtifactKind,
    ManagedArtifactRef,
)
from mastervault.change_control.managed_revision_materialization import (
    _grounded_citation,
    materialize_revision_planning_response,
)
from mastervault.change_control.managed_revision_planning import (
    RevisionPlanningCitationInput,
    RevisionPlanningCitationInputRole,
    RevisionPlanningCitationInputSet,
    RevisionPlanningCitationSelector,
)
from mastervault.change_control.managed_source_note import render_managed_source_note
from mastervault.change_control.recorded_inference import InferenceArtifactPayload
from mastervault.models import (
    Claim,
    Confidence,
    Domain,
    NoteStatus,
    NoteType,
    SourceNote,
    SourceType,
    content_hash,
)
from mastervault.vaultfs.frontmatter import (
    join_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)


class _SubstitutedManagedAnalysisSet(ManagedAnalysisSetBinding):
    pass


class _SubstitutedGenericAnalysisSet(GenericManagedAnalysisSetBindingV3):
    pass


def _predecessor_note(raw_text: str) -> bytes:
    note = SourceNote(
        domain=Domain.CUSTOMER_SUPPORT,
        type=NoteType.SOURCE,
        title="Returns guidance",
        tags=["returns", "policy"],
        status=NoteStatus.PROCESSED,
        created=date(2025, 1, 2),
        updated=date(2025, 2, 3),
        source_type=SourceType.POLICY,
        key_claims=[
            Claim(
                id="returns-window-01",
                statement="Premium customers may return items within 30 days.",
                confidence=Confidence.HIGH,
                affects=["returns-policy"],
            ),
            Claim(
                id="clearance-sale-02",
                statement="Clearance products remain final sale.",
                confidence=Confidence.MEDIUM,
                affects=["returns-policy"],
            ),
        ],
        provenance="runtime/raw/returns-v1.md",
        provenance_hash=content_hash(raw_text),
    )
    body = f"\n# {note.title}\n\n## Summary\n\nLegacy summary.\n\n## Content\n\n{raw_text}"
    return join_frontmatter(
        serialize_frontmatter(note.model_dump(mode="json", exclude_none=True)),
        body,
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("analysis_type", "substituted_type"),
    (
        (ManagedAnalysisSetBinding, _SubstitutedManagedAnalysisSet),
        (GenericManagedAnalysisSetBindingV3, _SubstitutedGenericAnalysisSet),
    ),
)
def test_materialization_dispatch_accepts_only_exact_analysis_set_variants(
    analysis_type: Any,
    substituted_type: Any,
) -> None:
    exact = analysis_type.model_construct()
    with pytest.raises(AttributeError, match="has no attribute"):
        materialize_revision_planning_response(
            workload=None,  # type: ignore[arg-type]
            shard=None,  # type: ignore[arg-type]
            response=None,  # type: ignore[arg-type]
            analysis_set=exact,
            predecessor_claims=(),
            envelope=None,  # type: ignore[arg-type]
            inference_artifacts=(),
        )

    substituted = substituted_type.model_construct()
    with pytest.raises(ValueError, match="requires non-empty impact authority"):
        materialize_revision_planning_response(
            workload=None,  # type: ignore[arg-type]
            shard=None,  # type: ignore[arg-type]
            response=None,  # type: ignore[arg-type]
            analysis_set=substituted,
            predecessor_claims=(),
            envelope=None,  # type: ignore[arg-type]
            inference_artifacts=(),
        )


def test_source_note_successor_preserves_schema_claim_order_and_exact_raw_content() -> None:
    raw = "Premium returns: 30 days.\nUnicode owner: Zoë 😀\n"
    successor_raw = raw.replace("30 days", "45 days").encode("utf-8")

    rendered = render_managed_source_note(
        predecessor_note_bytes=_predecessor_note(raw),
        successor_raw_bytes=successor_raw,
        successor_raw_path="runtime/raw/returns-v2.md",
        analysis_as_of=date(2026, 8, 9),
        statement_rewrites={
            "returns-window-01": "Premium customers may return items within 45 days."
        },
    )

    assert [item.id for item in rendered.model.key_claims] == [
        "returns-window-01",
        "clearance-sale-02",
    ]
    assert [item.statement for item in rendered.model.key_claims] == [
        "Premium customers may return items within 45 days.",
        "Clearance products remain final sale.",
    ]
    assert rendered.model.created == date(2025, 1, 2)
    assert rendered.model.updated == date(2026, 8, 9)
    assert rendered.model.provenance == "runtime/raw/returns-v2.md"
    assert rendered.model.provenance_hash == content_hash(successor_raw.decode("utf-8"))

    reopened_data, body = parse_frontmatter(rendered.note_bytes.decode("utf-8"))
    assert SourceNote.model_validate(reopened_data) == rendered.model
    _prefix, marker, exact_content = body.partition("\n## Content\n\n")
    assert marker
    assert exact_content.encode("utf-8") == successor_raw
    summary = body.split("\n\n## Content", maxsplit=1)[0]
    assert "Premium customers may return items within 45 days." in summary
    assert "Clearance products remain final sale." in summary


def test_python_character_selector_becomes_exact_utf8_byte_citation() -> None:
    text = "é😀 governing evidence"
    citation_input = RevisionPlanningCitationInput(
        input_selector="governing-evidence",
        role=RevisionPlanningCitationInputRole.GOVERNING_EVIDENCE,
        text_utf8=text,
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    artifact = ManagedArtifactRef.create(
        kind=ManagedArtifactKind.INFERENCE_INPUT,
        path=f"inference/citations/governing-evidence/{digest}.txt",
        sha256=digest,
        byte_count=len(text.encode("utf-8")),
    )
    payload = InferenceArtifactPayload(artifact=artifact, content_utf8=text)
    shard = SimpleNamespace(
        citation_inputs=RevisionPlanningCitationInputSet(inputs=(citation_input,))
    )

    citation = _grounded_citation(
        shard=shard,
        selector=RevisionPlanningCitationSelector(
            input_selector="governing-evidence",
            start_char=1,
            end_char=2,
        ),
        artifacts=(payload,),
    )

    assert citation.quote == "😀"
    assert citation.start_byte == len("é".encode())
    assert citation.end_byte == len("é😀".encode())
    assert citation.artifact_id == artifact.artifact_id
