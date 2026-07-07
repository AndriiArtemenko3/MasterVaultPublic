"""ingest/validate.py library API: autofix axis, hard-fail axis, idempotency."""

from __future__ import annotations

import pytest

from mastervault.config import IngestionCfg, Settings
from mastervault.ingest.validate import validate_source_note
from mastervault.vaultfs.frontmatter import parse_frontmatter

BROKEN_CLAIMS = (
    "key_claims:\n"
    "  - id: totally-wrong-id-07\n"
    '    statement: "Refunds  over 500 USD   require manager approval."\n'
    "    confidence: high\n"
    "    affects: ['[[Refund Policy]]', refund-policy, Refund_Policy]\n"
    "  - id: another-bad-id-99\n"
    '    statement: "Standard refunds settle within five business days."\n'
    "    confidence: medium\n"
    "    affects: [sla]\n"
)


class TestAutofixAxis:
    def test_dirty_without_fix(self, write_fixture, build_note, settings):
        path = write_fixture("refund-memo.md", build_note(BROKEN_CLAIMS))
        before = path.read_text(encoding="utf-8")
        report = validate_source_note(path, fix=False, settings=settings)
        assert report.status == "dirty"
        assert report.hard_fails == []
        assert report.autofixes
        assert path.read_text(encoding="utf-8") == before  # dry-run never writes

    def test_fix_canonicalizes(self, write_fixture, build_note, settings):
        path = write_fixture("refund-memo.md", build_note(BROKEN_CLAIMS))
        report = validate_source_note(path, fix=True, settings=settings)
        assert report.status == "fixed"

        data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        claims = data["key_claims"]
        assert [c["id"] for c in claims] == ["refund-memo-01", "refund-memo-02"]
        assert claims[0]["statement"] == "Refunds over 500 USD require manager approval."
        assert claims[0]["affects"] == ["refund-policy"]  # unwrapped, kebabed, deduped
        assert claims[1]["affects"] == ["sla"]
        # Everything outside key_claims survives.
        assert data["title"] == "Fixture Note"
        assert "Body paragraph stays untouched." in body

    def test_fix_is_idempotent(self, write_fixture, build_note, settings):
        path = write_fixture("refund-memo.md", build_note(BROKEN_CLAIMS))
        validate_source_note(path, fix=True, settings=settings)
        canonical = path.read_text(encoding="utf-8")

        report = validate_source_note(path, fix=True, settings=settings)
        assert report.status == "pass"
        assert report.autofixes == []
        assert path.read_text(encoding="utf-8") == canonical  # byte-identical

    def test_already_canonical_passes(self, write_fixture, build_note, build_claim, settings):
        claims = "key_claims:\n" + build_claim(
            "clean-note-01", "A perfectly canonical claim statement.", affects="[refund-policy]"
        )
        path = write_fixture("clean-note.md", build_note(claims))
        before = path.read_text(encoding="utf-8")
        report = validate_source_note(path, fix=True, settings=settings)
        assert report.status == "pass"
        assert path.read_text(encoding="utf-8") == before

    def test_no_key_claims_passes(self, write_fixture, build_note, settings):
        path = write_fixture("bare.md", build_note(""))
        assert validate_source_note(path, settings=settings).status == "pass"


class TestHardFailAxis:
    def _assert_hard(self, path, settings, needle):
        before = path.read_text(encoding="utf-8")
        report = validate_source_note(path, fix=True, settings=settings)
        assert report.status == "hard_fail"
        assert any(needle in h for h in report.hard_fails), report.hard_fails
        assert path.read_text(encoding="utf-8") == before  # hard fail never writes
        return report

    def test_empty_statement(self, write_fixture, build_note, build_claim, settings):
        path = write_fixture("n.md", build_note("key_claims:\n" + build_claim("n-01", "")))
        self._assert_hard(path, settings, "missing or empty")

    def test_statement_under_8_chars(self, write_fixture, build_note, build_claim, settings):
        path = write_fixture("n.md", build_note("key_claims:\n" + build_claim("n-01", "tiny")))
        self._assert_hard(path, settings, "shorter than 8")

    def test_statement_over_40_words(self, write_fixture, build_note, build_claim, settings):
        long_statement = " ".join(f"word{i}" for i in range(41))
        path = write_fixture(
            "n.md", build_note("key_claims:\n" + build_claim("n-01", long_statement))
        )
        self._assert_hard(path, settings, "> 40")

    def test_bad_confidence(self, write_fixture, build_note, build_claim, settings):
        path = write_fixture(
            "n.md",
            build_note(
                "key_claims:\n"
                + build_claim("n-01", "A statement long enough.", confidence="certain")
            ),
        )
        self._assert_hard(path, settings, "confidence")

    def test_too_many_claims(self, write_fixture, build_note, build_claim):
        tight = Settings(ingestion=IngestionCfg(max_claims_per_doc=2))
        claims = "key_claims:\n" + "".join(
            build_claim(f"n-{i:02d}", f"Distinct claim statement number {i}.")
            for i in range(1, 4)
        )
        path = write_fixture("n.md", build_note(claims))
        self._assert_hard(path, tight, "too many claims: 3 > 2")

    def test_duplicate_statements(self, write_fixture, build_note, build_claim, settings):
        claims = "key_claims:\n" + build_claim(
            "n-01", "Refund windows close after thirty days."
        ) + build_claim("n-02", "Refund  windows close after thirty DAYS.")
        path = write_fixture("n.md", build_note(claims))
        self._assert_hard(path, settings, "duplicate statement")

    def test_unparseable_frontmatter(self, write_fixture, settings):
        path = write_fixture("n.md", "---\ntitle: [unclosed\n---\n\nBody.\n")
        self._assert_hard(path, settings, "unparseable frontmatter")

    def test_key_claims_not_a_list(self, write_fixture, build_note, settings):
        path = write_fixture("n.md", build_note("key_claims: not-a-list\n"))
        self._assert_hard(path, settings, "expected list")

    def test_hard_fail_suppresses_write_despite_fixable_ids(
        self, write_fixture, build_note, build_claim, settings
    ):
        claims = "key_claims:\n" + build_claim(
            "wrong-id-42", "A valid statement with a fixable id.", affects="['[[Messy]]']"
        ) + build_claim("wrong-id-43", "tiny")
        path = write_fixture("n.md", build_note(claims))
        report = self._assert_hard(path, settings, "shorter than 8")
        assert report.autofixes  # findings still reported, just not applied


class TestSlugTailIds:
    def test_long_filename_ids_use_hashed_slug(self, write_fixture, build_note, settings):
        name = "a-very-long-filename-" + "x" * 80 + ".md"
        path = write_fixture(
            name,
            build_note(
                "key_claims:\n"
                "  - id: whatever-01\n"
                '    statement: "A statement long enough to pass."\n'
                "    confidence: low\n"
                "    affects: []\n"
            ),
        )
        validate_source_note(path, fix=True, settings=settings)
        data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        claim_id = data["key_claims"][0]["id"]
        assert claim_id.endswith("-01")
        assert len(claim_id) <= 60 + 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[[Refund Policy]]", "refund-policy"),
        ("Snake_Case_Slug", "snake-case-slug"),
        ("already-kebab", "already-kebab"),
    ],
)
def test_affects_normalization_shapes(
    raw, expected, write_fixture, build_note, build_claim, settings
):
    claims = "key_claims:\n" + build_claim(
        "n-01", "A statement long enough to pass.", affects=f"['{raw}']"
    )
    path = write_fixture("n.md", build_note(claims))
    validate_source_note(path, fix=True, settings=settings)
    data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    assert data["key_claims"][0]["affects"] == [expected]
