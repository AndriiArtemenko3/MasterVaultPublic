"""Agentic pipelines: ingest, ask, lint. Each composes contracts + storage +
retrieval + the review queue into one end-to-end run driven by a RunContext."""

from mastervault.pipelines.ask import AskOutcome, run_ask
from mastervault.pipelines.ingest import IngestOutcome, run_ingest
from mastervault.pipelines.lint import LintOutcome, run_lint

__all__ = [
    "AskOutcome",
    "IngestOutcome",
    "LintOutcome",
    "run_ask",
    "run_ingest",
    "run_lint",
]
