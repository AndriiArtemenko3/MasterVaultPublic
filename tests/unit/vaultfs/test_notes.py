"""notes.py: slugify, title extraction, typed read/write dispatch."""

from __future__ import annotations

import re
from datetime import date

import pytest

from mastervault.models import (
    Claim,
    Confidence,
    DecisionNote,
    Domain,
    NoteStatus,
    SourceNote,
    SourceType,
    StrategyNote,
    WikiEntry,
)
from mastervault.vaultfs.frontmatter import FrontmatterError
from mastervault.vaultfs.notes import extract_title, read_note, slugify, write_note

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello, World!") == "hello-world"
        assert slugify("  Already--kebab--case  ") == "already-kebab-case"

    def test_empty_input(self):
        assert slugify("!!!") == "untitled"

    def test_short_input_untouched(self):
        assert slugify("refund policy") == "refund-policy"

    def test_truncation_appends_hash_tail(self):
        long = "word " * 40
        slug = slugify(long, max_len=60)
        assert len(slug) <= 60
        assert SLUG_RE.match(slug)
        assert re.search(r"-[0-9a-f]{8}$", slug)

    def test_truncation_avoids_prefix_collisions(self):
        shared = "identical prefix " * 10
        a = slugify(shared + "variant one")
        b = slugify(shared + "variant two")
        assert a != b
        assert len(a) <= 60 and len(b) <= 60

    def test_deterministic(self):
        long = "the same very long title repeated " * 5
        assert slugify(long) == slugify(long)


class TestExtractTitle:
    def test_first_h1_wins(self):
        body = "intro line\n\n# Real Title\n\n# Second H1\n"
        assert extract_title(body, "fallback") == "Real Title"

    def test_fallback_when_no_h1(self):
        assert extract_title("## only an h2\n\ntext\n", "my-file") == "my-file"


class TestReadWriteDispatch:
    def _write(self, tmp_path, name, text):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_source_note_with_claims(self, tmp_path, source_note_text):
        p = self._write(tmp_path, "refund-note.md", source_note_text)
        loaded = read_note(p)
        assert isinstance(loaded.model, SourceNote)
        assert loaded.model.source_type == SourceType.CALL_TRANSCRIPT
        assert [c.id for c in loaded.model.key_claims] == ["refund-note-01", "refund-note-02"]
        assert loaded.body.lstrip("\n").startswith("# Refund policy call")

    def test_wiki_dispatch(self, tmp_path, make_note):
        p = self._write(tmp_path, "w.md", make_note(note_type="wiki"))
        assert isinstance(read_note(p).model, WikiEntry)

    def test_decision_dispatch(self, tmp_path, make_note):
        p = self._write(tmp_path, "d.md", make_note(note_type="decision"))
        model = read_note(p).model
        assert isinstance(model, DecisionNote)

    def test_strategy_dispatch(self, tmp_path, make_note):
        p = self._write(
            tmp_path, "s.md", make_note(note_type="strategy", extra_yaml="quarter: 2026-Q3\n")
        )
        model = read_note(p).model
        assert isinstance(model, StrategyNote)
        assert model.quarter == "2026-Q3"

    def test_unknown_type_raises(self, tmp_path, make_note):
        p = self._write(tmp_path, "x.md", make_note(note_type="journal"))
        with pytest.raises(FrontmatterError):
            read_note(p)

    def test_missing_title_filled_from_h1(self, tmp_path):
        text = (
            "---\ndomain: operations\ntype: wiki\nstatus: draft\n"
            "created: 2026-07-01\nupdated: 2026-07-01\n---\n\n# Onboarding Runbook\n\nText.\n"
        )
        p = self._write(tmp_path, "onboarding.md", text)
        assert read_note(p).model.title == "Onboarding Runbook"

    def test_missing_title_falls_back_to_filename(self, tmp_path):
        text = (
            "---\ndomain: operations\ntype: wiki\nstatus: draft\n"
            "created: 2026-07-01\nupdated: 2026-07-01\n---\n\nNo heading here.\n"
        )
        p = self._write(tmp_path, "bare-stub.md", text)
        assert read_note(p).model.title == "bare-stub"

    def test_write_then_read_round_trip(self, tmp_path):
        model = SourceNote(
            domain=Domain.SALES_CRM,
            title="Q3 pipeline review",
            source_type=SourceType.MEMO,
            tags=["pipeline"],
            status=NoteStatus.PROCESSED,
            created=date(2026, 7, 1),
            updated=date(2026, 7, 7),
            key_claims=[
                Claim(
                    id="q3-pipeline-01",
                    statement="Enterprise deals now close in 45 days on average.",
                    confidence=Confidence.MEDIUM,
                    affects=["sales-cycle"],
                )
            ],
        )
        body = "# Q3 pipeline review\n\nDeals are closing faster."
        p = tmp_path / "q3-pipeline.md"
        write_note(p, model, body)
        loaded = read_note(p)
        assert loaded.model == model
        assert loaded.body.strip("\n") == body
