"""Ingestion pipeline: raw-file conversion, claim extraction, concept
matching, corpus-check adjudication, wiki drafting, wikilink insertion, and
the claim schema gate (`validate`)."""

from mastervault.ingest.concept_match import MatchResult, match_claim
from mastervault.ingest.convert import discover_units, read_raw_text
from mastervault.ingest.corpus_check import AdjudicatedPair, adjudicate
from mastervault.ingest.extract import ExtractResult, extract_claims, guess_source_type
from mastervault.ingest.linker import LinkResult, insert_wikilink
from mastervault.ingest.validate import Report, validate_source_note
from mastervault.ingest.wiki_draft import DraftedWiki, draft_extend, draft_new_entry

__all__ = [
    "AdjudicatedPair",
    "DraftedWiki",
    "ExtractResult",
    "LinkResult",
    "MatchResult",
    "Report",
    "adjudicate",
    "discover_units",
    "draft_extend",
    "draft_new_entry",
    "extract_claims",
    "guess_source_type",
    "insert_wikilink",
    "match_claim",
    "read_raw_text",
    "validate_source_note",
]
