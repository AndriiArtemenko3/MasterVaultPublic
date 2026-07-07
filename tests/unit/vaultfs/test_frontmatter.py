"""frontmatter.py: split/parse/jam-repair/surgical replace."""

from __future__ import annotations

import pytest

from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    extract_field_block,
    parse_frontmatter,
    split_frontmatter,
    surgical_replace_field,
)


class TestSplitFrontmatter:
    def test_splits_yaml_and_body(self, source_note_text):
        yaml_str, body, had = split_frontmatter(source_note_text)
        assert had is True
        assert yaml_str.startswith("domain: customer-support\n")
        assert yaml_str.endswith("affects: [refund-policy, sla]\n")
        assert body.startswith("\n# Refund policy call")

    def test_no_frontmatter(self):
        text = "# Just a heading\n\nBody.\n"
        yaml_str, body, had = split_frontmatter(text)
        assert (yaml_str, body, had) == ("", text, False)

    def test_unclosed_fence_is_not_frontmatter(self):
        text = "---\ntitle: x\nno closing fence\n"
        yaml_str, body, had = split_frontmatter(text)
        assert (yaml_str, body, had) == ("", text, False)

    def test_empty_body(self):
        text = "---\ntitle: x\n---\n"
        yaml_str, body, had = split_frontmatter(text)
        assert had is True
        assert yaml_str == "title: x\n"
        assert body == ""


class TestParseFrontmatter:
    def test_happy_path(self, source_note_text):
        data, body = parse_frontmatter(source_note_text)
        assert data["domain"] == "customer-support"
        assert len(data["key_claims"]) == 2
        assert body.lstrip("\n").startswith("# Refund policy call")

    def test_jam_repair_unquoted_colon_in_value(self):
        text = "---\ntitle: Postmortem: the June outage\ntags: [ops]\n---\n\nBody.\n"
        data, _body = parse_frontmatter(text)
        assert data["title"] == "Postmortem: the June outage"
        assert data["tags"] == ["ops"]

    def test_unrepairable_yaml_raises(self):
        text = "---\ntitle: [unclosed\n---\n\nBody.\n"
        with pytest.raises(FrontmatterError):
            parse_frontmatter(text)

    def test_missing_frontmatter_raises(self):
        with pytest.raises(FrontmatterError):
            parse_frontmatter("no fences here\n")


class TestSurgicalReplaceField:
    def test_replace_with_current_value_is_byte_identical(self, source_note_text):
        for fld in ("domain", "tags", "key_claims", "updated"):
            block = extract_field_block(source_note_text, fld)
            assert surgical_replace_field(source_note_text, fld, block) == source_note_text

    def test_replace_last_field_is_byte_identical(self, make_note):
        text = make_note(extra_yaml="aliases: [alpha, beta]\n")
        block = extract_field_block(text, "aliases")
        assert surgical_replace_field(text, "aliases", block) == text

    def test_replace_scalar_field(self, source_note_text):
        result = surgical_replace_field(source_note_text, "status", "status: processed\n")
        assert result == source_note_text.replace("status: draft", "status: processed")

    def test_replace_multiline_block_preserves_everything_else(self, source_note_text):
        new_block = (
            "key_claims:\n"
            "  - id: refund-note-01\n"
            '    statement: "Rewritten claim statement."\n'
            "    confidence: low\n"
            "    affects: []\n"
        )
        result = surgical_replace_field(source_note_text, "key_claims", new_block)
        old_block = extract_field_block(source_note_text, "key_claims")
        start = source_note_text.index(old_block)
        assert result[:start] == source_note_text[:start]
        assert result[start : start + len(new_block)] == new_block
        assert result[start + len(new_block) :] == source_note_text[start + len(old_block) :]

    def test_missing_field_raises_keyerror(self, source_note_text):
        with pytest.raises(KeyError):
            surgical_replace_field(source_note_text, "aliases", "aliases: []\n")

    def test_body_lookalike_lines_are_not_matched(self, make_note):
        text = make_note(body="status: this-is-body-text\n\nMore body.")
        result = surgical_replace_field(text, "status", "status: archived\n")
        assert "status: this-is-body-text" in result
        assert "status: archived\n" in result

    def test_no_frontmatter_raises(self):
        with pytest.raises(FrontmatterError):
            surgical_replace_field("plain body\n", "status", "status: draft\n")

    def test_trailing_newline_added_to_block(self, source_note_text):
        result = surgical_replace_field(source_note_text, "status", "status: archived")
        assert result == source_note_text.replace("status: draft", "status: archived")
