"""End-to-end `mvault ask` via the real CLI: init -> write vault -> sync ->
ask. Hermetic through env-var settings overrides (mock embedding + mock LLM,
sqlite backend, tmp workspace), the same pattern `test_cli_review.py` uses.

The CLI resolves its own fresh `MockLLM()` per invocation (via
`providers.get_llm`), so scripted judge/synthesis responses can't be
pre-pushed from the test process — this exercises exactly the "cold mock"
path: an empty push-registry naturally falls through judge hard-fail and
synthesis hard-fail to the deterministic EXTRACTIVE fallback, over real
evidence synced from a real vault note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.cli.app import app

runner = CliRunner()

WIKI_NOTE = """---
domain: operations
type: wiki
title: Refund Policy
aliases:
  - refund policy
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Refund Policy

## Definition

Refunds are issued within 30 days of delivery for unused items.
"""

SOURCE_NOTE = """---
domain: operations
type: source
source_type: faq
title: Refund FAQ
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
key_claims:
  - id: refund-faq-01
    statement: "Refunds are issued within 30 days of delivery for unused items."
    confidence: high
    affects: [refund-policy]
---

# Refund FAQ

Refunds are issued within 30 days of delivery for unused items.
"""


@pytest.fixture
def cli_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings at an isolated tmp workspace with mock providers."""
    empty_toml = tmp_path / "empty.toml"
    empty_toml.write_text("", encoding="utf-8")
    ws = tmp_path / "ws"
    monkeypatch.setenv("MV_CONFIG", str(empty_toml))
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(ws))
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return ws


def _seed_and_sync(cli_workspace: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    vault_dir = cli_workspace / "vault"
    (vault_dir / "operations" / "wiki").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "sources").mkdir(parents=True, exist_ok=True)
    (vault_dir / "operations" / "wiki" / "refund-policy.md").write_text(WIKI_NOTE, encoding="utf-8")
    (vault_dir / "operations" / "sources" / "refund-faq.md").write_text(SOURCE_NOTE, encoding="utf-8")

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output


def test_ask_cli_extractive_fallback_over_real_evidence(cli_workspace: Path):
    _seed_and_sync(cli_workspace)

    result = runner.invoke(app, ["ask", "What is the refund policy?"])

    assert result.exit_code == 0, result.output
    assert "Extractive answer (no generative synthesis):" in result.output
    assert "30 days" in result.output
    assert "Sources:" in result.output


def test_ask_cli_json_output_shape(cli_workspace: Path):
    _seed_and_sync(cli_workspace)

    result = runner.invoke(app, ["ask", "What is the refund policy?", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["extractive"] is True
    assert payload["zero_evidence"] is False
    assert payload["rounds"] >= 1
    assert any("30 days" in s for s in [payload["answer_markdown"]])
    assert payload["sources"]


def test_ask_cli_zero_evidence_before_any_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    empty_toml = tmp_path / "empty.toml"
    empty_toml.write_text("", encoding="utf-8")
    ws = tmp_path / "ws"
    monkeypatch.setenv("MV_CONFIG", str(empty_toml))
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(ws))
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "mock")
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["ask", "Anything at all?"])

    assert result.exit_code == 0, result.output
    assert "corpus has no grounding for this question" in result.output
