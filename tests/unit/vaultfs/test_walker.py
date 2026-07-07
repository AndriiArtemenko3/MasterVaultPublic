"""walker.py: enumeration, dotfile/non-md exclusion, skip reasons, hashing."""

from __future__ import annotations

import pytest

from mastervault.models import Domain, NoteType, content_hash
from mastervault.vaultfs.walker import walk_vault


@pytest.fixture
def vault(tmp_path, make_note):
    (tmp_path / "wiki").mkdir()
    (tmp_path / ".obsidian").mkdir()

    (tmp_path / "wiki" / "refund-policy.md").write_text(
        make_note(domain="customer-support", note_type="wiki", title="Refund Policy"),
        encoding="utf-8",
    )
    (tmp_path / "ops-memo.md").write_text(
        make_note(domain="operations", note_type="source", title="Ops Memo"),
        encoding="utf-8",
    )
    # Ignored silently: not markdown, dotfile, inside a dot-directory.
    (tmp_path / "readme.txt").write_text("not a note", encoding="utf-8")
    (tmp_path / ".draft.md").write_text(make_note(), encoding="utf-8")
    (tmp_path / ".obsidian" / "workspace.md").write_text(make_note(), encoding="utf-8")
    # Skipped with reasons.
    (tmp_path / "broken-yaml.md").write_text(
        "---\ntitle: [unclosed\n---\n\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "no-domain.md").write_text(
        "---\ntype: wiki\ntitle: Missing Domain\n---\n\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "bad-domain.md").write_text(
        make_note(domain="sixth-domain"), encoding="utf-8"
    )
    (tmp_path / "bad-type.md").write_text(make_note(note_type="journal"), encoding="utf-8")
    (tmp_path / "no-frontmatter.md").write_text("# Loose file\n\nBody.\n", encoding="utf-8")
    return tmp_path


def test_walk_collects_valid_notes(vault):
    result = walk_vault(vault)
    by_rel = {n.rel_path: n for n in result.notes}
    assert set(by_rel) == {"wiki/refund-policy.md", "ops-memo.md"}

    ref = by_rel["wiki/refund-policy.md"]
    assert ref.abs_path == vault / "wiki" / "refund-policy.md"
    assert ref.note_type == NoteType.WIKI
    assert ref.domain == Domain.CUSTOMER_SUPPORT
    assert ref.content_hash == content_hash(ref.abs_path.read_text(encoding="utf-8"))


def test_walk_skip_reasons(vault):
    result = walk_vault(vault)
    reasons = dict(result.skipped)
    assert set(reasons) == {
        "broken-yaml.md",
        "no-domain.md",
        "bad-domain.md",
        "bad-type.md",
        "no-frontmatter.md",
    }
    assert "unparseable" in reasons["broken-yaml.md"]
    assert "missing required fields: domain" in reasons["no-domain.md"]
    assert "invalid domain" in reasons["bad-domain.md"]
    assert "invalid type" in reasons["bad-type.md"]
    assert "no frontmatter" in reasons["no-frontmatter.md"]


def test_walk_is_sorted_and_deterministic(vault):
    first = walk_vault(vault)
    second = walk_vault(vault)
    assert first == second
    rels = [n.rel_path for n in first.notes]
    assert rels == sorted(rels)


def test_walk_empty_dir(tmp_path):
    result = walk_vault(tmp_path)
    assert result.notes == [] and result.skipped == []
