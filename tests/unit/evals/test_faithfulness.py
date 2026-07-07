"""Mechanical citation-validity checker: synthetic answer + evidence pool."""

from __future__ import annotations

from mastervault.evals.faithfulness import check_citations, extract_citations


class TestExtractCitations:
    def test_extracts_every_bracket_token_in_order(self):
        text = "Refunds take 45 days [claim:policy-01], not 30 [claim:faq-02]."
        assert extract_citations(text) == ["claim:policy-01", "claim:faq-02"]

    def test_no_citations_returns_empty_list(self):
        assert extract_citations("No brackets here.") == []

    def test_duplicate_citation_kept_as_separate_occurrences(self):
        text = "First [claim:a]. Again, [claim:a]."
        assert extract_citations(text) == ["claim:a", "claim:a"]


class TestCheckCitations:
    def test_all_citations_valid(self):
        answer = "Returns are 45 days [claim:policy-sl2-v2-01], permanent [claim:policy-sl2-v2-04]."
        pool = {"claim:policy-sl2-v2-01", "claim:policy-sl2-v2-04", "claim:other-05"}
        report = check_citations(answer, pool)
        assert report.valid is True
        assert report.cited_ids == ["claim:policy-sl2-v2-01", "claim:policy-sl2-v2-04"]
        assert report.valid_ids == ["claim:policy-sl2-v2-01", "claim:policy-sl2-v2-04"]
        assert report.invalid_ids == []
        assert report.precision == 1.0

    def test_hallucinated_citation_is_invalid(self):
        answer = "The window is 45 days [claim:policy-sl2-v2-01], per legal review [claim:made-up-99]."
        pool = {"claim:policy-sl2-v2-01"}
        report = check_citations(answer, pool)
        assert report.valid is False
        assert report.valid_ids == ["claim:policy-sl2-v2-01"]
        assert report.invalid_ids == ["claim:made-up-99"]
        assert report.precision == 0.5

    def test_no_citations_at_all_is_vacuously_valid(self):
        report = check_citations("A confident but uncited answer.", {"claim:a"})
        assert report.cited_ids == []
        assert report.valid is True
        assert report.precision == 1.0

    def test_repeated_invalid_citation_counted_once_in_invalid_ids(self):
        answer = "Claim one [claim:ghost]. Claim two [claim:ghost]."
        report = check_citations(answer, {"claim:real"})
        assert report.invalid_ids == ["claim:ghost"]
        assert report.cited_ids == ["claim:ghost", "claim:ghost"]
        assert report.precision == 0.0

    def test_empty_pool_makes_every_citation_invalid(self):
        report = check_citations("Grounded [claim:a] and [claim:b].", set())
        assert report.valid is False
        assert set(report.invalid_ids) == {"claim:a", "claim:b"}

    def test_wiki_and_chunk_style_ids_resolve_too(self):
        answer = "See [wiki:customer-support:return-policy] and [chunk:source:foo.md#0]."
        pool = {"wiki:customer-support:return-policy", "chunk:source:foo.md#0"}
        report = check_citations(answer, pool)
        assert report.valid is True
