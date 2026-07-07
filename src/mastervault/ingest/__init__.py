"""Ingestion pipeline. Currently: the claim schema gate (`validate`)."""

from mastervault.ingest.validate import Report, validate_source_note

__all__ = ["Report", "validate_source_note"]
