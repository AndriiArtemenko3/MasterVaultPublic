"""`affects:` reconciliation: drop dangling wiki references, never invent them."""

from __future__ import annotations

from pathlib import Path

from mastervault.ingest.affects import existing_wiki_slugs, reconcile_affects
from mastervault.vaultfs.frontmatter import parse_frontmatter

KNOWN = {"refund-policy", "free-shipping", "return-policy"}


def note(*claims: str, body: str = "# Doc\n\nBody text.\n") -> str:
    blocks = "\n".join(claims)
    return (
        "---\n"
        "domain: customer-support\n"
        "type: source\n"
        "title: A Note\n"
        "status: processed\n"
        "created: '2026-03-01'\n"
        "updated: '2026-03-01'\n"
        "source_type: faq\n"
        "key_claims:\n"
        f"{blocks}\n"
        "provenance: datasets/raw/a.md\n"
        "provenance_hash: deadbeefdeadbeef\n"
        "---\n"
        f"{body}"
    )


def claim(cid: str, statement: str, affects: list[str]) -> str:
    lines = [f"- id: {cid}", f"  statement: {statement}", "  confidence: high"]
    if affects:
        lines.append("  affects:")
        lines.extend(f"  - {slug}" for slug in affects)
    else:
        lines.append("  affects: []")
    return "\n".join(lines)


def affects_of(text: str) -> list[list[str]]:
    data, _ = parse_frontmatter(text)
    return [list(c.get("affects") or []) for c in data["key_claims"]]


class TestReconcileAffects:
    def test_drops_only_the_unknown_slug_and_keeps_the_known_one(self):
        text = note(claim("a-01", "Refunds take five days.", ["refund-policy", "refund-timing"]))
        repair = reconcile_affects(text, KNOWN)
        assert repair.changed
        assert affects_of(repair.text) == [["refund-policy"]]
        assert [(d.claim_id, d.slug) for d in repair.dropped] == [("a-01", "refund-timing")]

    def test_a_claim_losing_every_slug_keeps_a_valid_empty_list(self):
        text = note(claim("a-01", "Parts ship in two days.", ["shipping", "warranty"]))
        repair = reconcile_affects(text, KNOWN)
        assert affects_of(repair.text) == [[]]
        # `affects:` with nothing under it would parse as None and fail the
        # SourceNote model; the empty list must be explicit.
        assert "affects: []" in repair.text
        assert {d.slug for d in repair.dropped} == {"shipping", "warranty"}

    def test_a_clean_note_is_returned_byte_for_byte(self):
        text = note(
            claim("a-01", "Refunds take five days.", ["refund-policy"]),
            claim("a-02", "Shipping is free over 75.", ["free-shipping"]),
        )
        repair = reconcile_affects(text, KNOWN)
        assert not repair.changed
        assert repair.dropped == []
        assert repair.text == text

    def test_a_near_miss_is_dropped_not_remapped(self):
        """`shipping` looks like `free-shipping` but is a different concept."""
        text = note(claim("a-01", "Orders ship quickly.", ["shipping"]))
        repair = reconcile_affects(text, KNOWN)
        assert affects_of(repair.text) == [[]]
        assert "free-shipping" not in repair.text

    def test_untouched_claims_keep_their_exact_bytes(self):
        clean = claim("a-01", "Refunds take five days.", ["refund-policy"])
        text = note(clean, claim("a-02", "Orders ship quickly.", ["shipping"]))
        repair = reconcile_affects(text, KNOWN)
        assert clean in repair.text
        assert affects_of(repair.text) == [["refund-policy"], []]

    def test_body_is_never_touched(self):
        text = note(claim("a-01", "Orders ship quickly.", ["shipping"]), body="# Title\n\nKeep me.\n")
        repair = reconcile_affects(text, KNOWN)
        assert repair.text.endswith("# Title\n\nKeep me.\n")

    def test_note_without_key_claims_is_unchanged(self):
        text = "---\ndomain: customer-support\ntype: wiki\ntitle: W\n---\n\nBody\n"
        repair = reconcile_affects(text, KNOWN)
        assert repair.text == text
        assert not repair.changed

    def test_unparseable_frontmatter_is_left_to_the_frontmatter_gate(self):
        text = "no frontmatter at all\n"
        repair = reconcile_affects(text, KNOWN)
        assert repair.text == text
        assert not repair.changed

    def test_is_idempotent(self):
        text = note(claim("a-01", "Orders ship quickly.", ["shipping", "refund-policy"]))
        once = reconcile_affects(text, KNOWN)
        twice = reconcile_affects(once.text, KNOWN)
        assert twice.text == once.text
        assert not twice.changed

    def test_empty_known_set_drops_everything(self):
        text = note(claim("a-01", "Refunds take five days.", ["refund-policy"]))
        repair = reconcile_affects(text, set())
        assert affects_of(repair.text) == [[]]


class TestExistingWikiSlugs:
    def test_reads_wiki_stems_off_the_vault_tree(self, tmp_path: Path):
        wiki = tmp_path / "customer-support" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "refund-policy.md").write_text(
            "---\ndomain: customer-support\ntype: wiki\ntitle: Refund Policy\n"
            "status: processed\ncreated: '2026-03-01'\nupdated: '2026-03-01'\n"
            "aliases: [refund policy]\n---\n\nDefinition.\n",
            encoding="utf-8",
        )
        sources = tmp_path / "customer-support" / "sources"
        sources.mkdir(parents=True)
        (sources / "a-source.md").write_text(
            note(claim("a-01", "Refunds take five days.", ["refund-policy"])), encoding="utf-8"
        )

        # Only wiki notes count: a source note's stem is not a concept slug.
        assert existing_wiki_slugs(tmp_path) == {"refund-policy"}
