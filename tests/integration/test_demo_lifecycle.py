"""Integration test for `mvault demo {status,reset,delete}` against the real
shipped Larkstead dataset.

Sequence: init -> status (not loaded) -> load -> status (matches the shipped
counts) -> mutate the workspace by hand (edit a copied source file, approve
one of the seeded review items) -> status (now differs) -> reset (back to
pristine, idempotent) -> status (matches again) -> delete (workspace gone,
idempotent).

Uses sqlite + the local embedding provider (matches the sidecar's model, so
`demo load`/`demo reset` never touch the network) + the mock LLM provider
(review approve/apply is pure file surgery and never calls the LLM).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "larkstead"

# Stable dataset facts — see tests/integration/test_demo_load.py for the same
# constants sourced from datasets/larkstead/processed/MANIFEST.md and
# datasets/larkstead/embeddings/manifest.json.
EXPECTED_DOCUMENTS = 409
EXPECTED_CLAIMS = 3412
EXPECTED_WIKI = 43
EXPECTED_CHUNKS = 1897
EXPECTED_EMBEDDINGS = 5352
EXPECTED_PENDING_REVIEW = 4


def _set_env(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("MV_STORAGE__BACKEND", "sqlite")
    monkeypatch.setenv("MV_EMBEDDING__PROVIDER", "local")  # matches the sidecar's model
    monkeypatch.setenv("MV_LLM__PROVIDER", "mock")
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(workspace))
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_demo_lifecycle_load_status_mutate_reset_delete(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    _set_env(monkeypatch, workspace)

    from mastervault.cli.app import app

    runner = CliRunner()

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    # -- status before any load: clean "not loaded" signal, exit 1 -------------
    result = runner.invoke(app, ["demo", "status"])
    assert result.exit_code == 1
    assert "demo not loaded" in result.output

    # -- load --------------------------------------------------------------
    result = runner.invoke(app, ["demo", "load"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["demo", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["loaded"] is True
    assert payload["live"] == payload["expected"]
    assert payload["live"]["documents"] == EXPECTED_DOCUMENTS
    assert payload["live"]["claims"] == EXPECTED_CLAIMS
    assert payload["live"]["wiki"] == EXPECTED_WIKI
    assert payload["live"]["chunks"] == EXPECTED_CHUNKS
    assert payload["live"]["embeddings"] == EXPECTED_EMBEDDINGS
    assert payload["live"]["pending_review"] == EXPECTED_PENDING_REVIEW

    # human-readable status agrees: every row matches
    human = runner.invoke(app, ["demo", "status"])
    assert human.exit_code == 0, human.output
    assert "differs" not in human.output
    assert "fully imported, matches the shipped sidecar" in human.output

    # -- mutate the workspace by hand ---------------------------------------
    edited_path = next((workspace / "vault" / "customer-support" / "sources").glob("*.md"))
    original_text = edited_path.read_text(encoding="utf-8")
    edited_path.write_text(original_text + "\nHAND-EDITED BY USER\n", encoding="utf-8")

    list_result = runner.invoke(app, ["review", "list", "--json"])
    assert list_result.exit_code == 0, list_result.output
    items = json.loads(list_result.output)
    assert len(items) == EXPECTED_PENDING_REVIEW  # the 4 seeded contradictions
    item_id = items[0]["id"]

    approve_result = runner.invoke(app, ["review", "approve", item_id, "--yes"])
    assert approve_result.exit_code == 0, approve_result.output
    assert (workspace / "review" / "archive" / f"{item_id}.md").is_file()
    assert not (workspace / "review" / "pending" / f"{item_id}.md").is_file()

    result = runner.invoke(app, ["demo", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["live"] != payload["expected"]
    assert payload["live"]["pending_review"] == EXPECTED_PENDING_REVIEW - 1
    assert "HAND-EDITED BY USER" in edited_path.read_text(encoding="utf-8")

    human = runner.invoke(app, ["demo", "status"])
    assert human.exit_code == 0, human.output
    assert "differs" in human.output

    # -- reset: discards the edit + the approval, restores the sidecar -------
    reset_result = runner.invoke(app, ["demo", "reset", "--yes"])
    assert reset_result.exit_code == 0, reset_result.output

    assert not (workspace / "review" / "archive" / f"{item_id}.md").is_file()
    assert (workspace / "review" / "pending" / f"{item_id}.md").is_file()
    assert "HAND-EDITED BY USER" not in edited_path.read_text(encoding="utf-8")
    assert edited_path.read_text(encoding="utf-8") == original_text

    result = runner.invoke(app, ["demo", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["live"] == payload["expected"]
    assert payload["live"]["embeddings"] == EXPECTED_EMBEDDINGS

    # reset is idempotent: running it again on an already-pristine workspace
    # succeeds and leaves the same counts.
    reset_again = runner.invoke(app, ["demo", "reset", "--yes"])
    assert reset_again.exit_code == 0, reset_again.output
    result = runner.invoke(app, ["demo", "status", "--json"])
    payload = json.loads(result.output)
    assert payload["live"] == payload["expected"]

    # -- delete: the whole workspace goes away -------------------------------
    delete_result = runner.invoke(app, ["demo", "delete", "--yes"])
    assert delete_result.exit_code == 0, delete_result.output
    assert not workspace.exists()

    # delete is idempotent: a second run against an already-deleted workspace
    # is a clean no-op, not an error.
    delete_again = runner.invoke(app, ["demo", "delete", "--yes"])
    assert delete_again.exit_code == 0, delete_again.output
    assert "nothing to delete" in delete_again.output
