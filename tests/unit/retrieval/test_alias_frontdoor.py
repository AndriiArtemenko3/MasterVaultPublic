"""Alias front-door: longest-alias-first matching on word boundaries."""

from __future__ import annotations

from mastervault.retrieval.channels import alias_frontdoor


class StubBackend:
    """Only the alias_index() surface the front-door touches."""

    def __init__(self, aliases: dict[str, tuple[str, str]]):
        self._aliases = aliases

    def alias_index(self) -> dict[str, tuple[str, str]]:
        return self._aliases


ALIASES = {
    "refund": ("refund-basics", "customer-support"),
    "refund window": ("refund-window", "customer-support"),
    "fee": ("restocking-fee", "customer-support"),
}


def test_longest_alias_wins():
    doc_id, alias = alias_frontdoor("What is the refund window policy?", StubBackend(ALIASES))
    assert doc_id == "wiki:customer-support:refund-window"
    assert alias == "refund window"


def test_shorter_alias_matches_when_longer_absent():
    doc_id, alias = alias_frontdoor("refund rules for gifts", StubBackend(ALIASES))
    assert doc_id == "wiki:customer-support:refund-basics"
    assert alias == "refund"


def test_word_boundary_negative():
    # "fee" is inside "coffee" but not on a word boundary -> no match
    assert alias_frontdoor("the coffee machine broke", StubBackend(ALIASES)) == (None, None)


def test_word_boundary_positive():
    doc_id, alias = alias_frontdoor("does the fee apply here", StubBackend(ALIASES))
    assert doc_id == "wiki:customer-support:restocking-fee"
    assert alias == "fee"


def test_case_insensitive():
    doc_id, _ = alias_frontdoor("REFUND WINDOW extension", StubBackend(ALIASES))
    assert doc_id == "wiki:customer-support:refund-window"


def test_no_aliases_and_no_match():
    assert alias_frontdoor("anything", StubBackend({})) == (None, None)
    assert alias_frontdoor("unrelated query", StubBackend(ALIASES)) == (None, None)
