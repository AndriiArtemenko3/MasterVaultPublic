"""Builders for claim-gate tests. Hermetic: tmp_path fixtures, explicit Settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from mastervault.config import IngestionCfg, Settings

FM_HEAD = (
    "---\n"
    "domain: operations\n"
    "type: source\n"
    "title: Fixture Note\n"
    "source_type: memo\n"
    "tags: [fixture]\n"
    "status: draft\n"
    "created: 2026-07-01\n"
    "updated: 2026-07-01\n"
)

BODY = "---\n\n# Fixture Note\n\nBody paragraph stays untouched.\n"


def note_with_claims(claims_yaml: str) -> str:
    """Full note text with the given raw `key_claims:` block spliced in."""
    return FM_HEAD + claims_yaml + BODY


def claim_yaml(
    ident: str,
    statement: str,
    confidence: str = "high",
    affects: str = "[]",
) -> str:
    return (
        f"  - id: {ident}\n"
        f'    statement: "{statement}"\n'
        f"    confidence: {confidence}\n"
        f"    affects: {affects}\n"
    )


@pytest.fixture
def build_note():
    """note_with_claims builder as a fixture (avoids cross-dir conftest imports)."""
    return note_with_claims


@pytest.fixture
def build_claim():
    return claim_yaml


@pytest.fixture
def settings() -> Settings:
    """Explicit settings so tests never depend on env vars or a config file."""
    return Settings(ingestion=IngestionCfg(max_claims_per_doc=10))


@pytest.fixture
def write_fixture(tmp_path):
    def _write(name: str, text: str) -> Path:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    return _write
