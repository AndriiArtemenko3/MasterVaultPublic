"""cli/review.py via typer.testing.CliRunner — hermetic against a tmp workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mastervault.cli.review import review_app
from mastervault.core.errors import EXIT_CODES
from mastervault.models import ReviewStatus, content_hash
from mastervault.review.queue import ReviewQueue

runner = CliRunner()

NOTE = """---
domain: operations
type: wiki
title: Refund Policy
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Refund Policy

Old body.
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch, fixed_clock) -> dict:
    """Point Settings at a tmp workspace and seed one vault note."""
    empty_toml = tmp_path / "empty.toml"
    empty_toml.write_text("")
    ws = tmp_path / "ws"
    monkeypatch.setenv("MV_CONFIG", str(empty_toml))
    monkeypatch.setenv("MV_PATHS__WORKSPACE", str(ws))

    target = ws / "vault" / "wiki" / "refund-policy.md"
    target.parent.mkdir(parents=True)
    target.write_text(NOTE, encoding="utf-8")

    queue = ReviewQueue(ws / "review" / "pending", ws / "review" / "archive", clock=fixed_clock)
    return {"queue": queue, "target": target, "root": ws}


def seed(workspace, make_item, *, id: str, tier: int = 2, pattern: str = "wiki-body-edit") -> Path:
    item = make_item(
        id=id,
        tier=tier,
        pattern_key=pattern,
        base_hash=content_hash(NOTE),
        payload={"mode": "full_file"},
    )
    path = workspace["queue"].enqueue(item, f"# Refund Policy\n\nNew body via {id}.", "replace")
    assert path is not None
    return path


# -- list ---------------------------------------------------------------------


def test_list_table_and_json(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    seed(workspace, make_item, id="rv-0002-beta", pattern="other-pattern")

    table = runner.invoke(review_app, ["list"])
    assert table.exit_code == 0

    as_json = runner.invoke(review_app, ["list", "--json"])
    assert as_json.exit_code == 0
    rows = json.loads(as_json.output)
    assert {r["id"] for r in rows} == {"rv-0001-alpha", "rv-0002-beta"}

    filtered = runner.invoke(review_app, ["list", "--json", "--pattern", "other-pattern"])
    assert [r["id"] for r in json.loads(filtered.output)] == ["rv-0002-beta"]


def test_list_rejects_unknown_status(workspace):
    result = runner.invoke(review_app, ["list", "--status", "bogus"])
    assert result.exit_code == EXIT_CODES["usage"]


# -- show ---------------------------------------------------------------------


def test_show_by_id_prefix(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    result = runner.invoke(review_app, ["show", "rv-0001"])
    assert result.exit_code == 0
    assert "rv-0001-alpha" in result.output
    assert "## Proposal (replace)" in result.output
    assert "New body via rv-0001-alpha." in result.output


def test_show_unknown_id_is_usage_error(workspace):
    result = runner.invoke(review_app, ["show", "rv-9999"])
    assert result.exit_code == EXIT_CODES["usage"]


def test_ambiguous_prefix_is_usage_error(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    seed(workspace, make_item, id="rv-0001-beta")
    result = runner.invoke(review_app, ["show", "rv-0001"])
    assert result.exit_code == EXIT_CODES["usage"]


# -- approve / reject ------------------------------------------------------------


def test_approve_applies_and_archives(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    result = runner.invoke(review_app, ["approve", "rv-0001"])
    assert result.exit_code == 0
    assert "New body via rv-0001-alpha." in workspace["target"].read_text()
    assert workspace["queue"].list_items() == []
    archived = list((workspace["root"] / "review" / "archive").glob("*.md"))
    assert len(archived) == 1


def test_approve_tier3_requires_yes(workspace, make_item):
    seed(workspace, make_item, id="rv-0003-gamma", tier=3)

    refused = runner.invoke(review_app, ["approve", "rv-0003"])
    assert refused.exit_code == EXIT_CODES["usage"]
    assert "Old body." in workspace["target"].read_text()  # nothing applied

    allowed = runner.invoke(review_app, ["approve", "rv-0003", "--yes"])
    assert allowed.exit_code == 0
    assert "New body via rv-0003-gamma." in workspace["target"].read_text()


def test_approve_conflict_exits_nonzero(workspace, make_item):
    path = seed(workspace, make_item, id="rv-0001-alpha")
    workspace["target"].write_text(NOTE + "\ndrifted\n")
    result = runner.invoke(review_app, ["approve", "rv-0001"])
    assert result.exit_code == EXIT_CODES["completed-with-failures"]
    assert path.exists()  # conflict stays pending-side
    assert workspace["queue"].load(path).item.status == ReviewStatus.CONFLICT


def test_reject_requires_reason_and_archives(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")

    missing = runner.invoke(review_app, ["reject", "rv-0001"])
    assert missing.exit_code != 0  # --reason is required

    result = runner.invoke(review_app, ["reject", "rv-0001", "--reason", "stale proposal"])
    assert result.exit_code == 0
    assert workspace["queue"].list_items() == []
    archived = (workspace["root"] / "review" / "archive") / "rv-0001-alpha.md"
    loaded = workspace["queue"].load(archived)
    assert loaded.item.status == ReviewStatus.REJECTED
    assert loaded.resolution == "stale proposal"
    assert "Old body." in workspace["target"].read_text()  # never applied


# -- approve-pattern / spot-check ---------------------------------------------------


def test_approve_pattern_applies_whole_group(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    # After the first apply the target changes, so the second item must be
    # proposed against the drifted content to apply cleanly; keep it simple:
    # same pattern, different target-agnostic check via conflict count.
    result = runner.invoke(review_app, ["approve-pattern", "wiki-body-edit"])
    assert result.exit_code == 0
    assert "1 applied, 0 conflicts" in result.output


def test_approve_pattern_refuses_tier3_in_group(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    seed(workspace, make_item, id="rv-0003-gamma", tier=3)
    result = runner.invoke(review_app, ["approve-pattern", "wiki-body-edit"])
    assert result.exit_code == EXIT_CODES["usage"]
    assert len(workspace["queue"].list_items()) == 2  # untouched


def test_approve_pattern_unknown_pattern_is_usage_error(workspace):
    result = runner.invoke(review_app, ["approve-pattern", "no-such-pattern"])
    assert result.exit_code == EXIT_CODES["usage"]


def test_spot_check_decline_applies_nothing(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    result = runner.invoke(review_app, ["spot-check", "wiki-body-edit"], input="n\n")
    assert result.exit_code == 0
    assert "nothing applied" in result.output
    assert len(workspace["queue"].list_items()) == 1


def test_spot_check_confirm_applies_group(workspace, make_item):
    seed(workspace, make_item, id="rv-0001-alpha")
    result = runner.invoke(review_app, ["spot-check", "wiki-body-edit"], input="y\n")
    assert result.exit_code == 0
    assert "New body via rv-0001-alpha." in workspace["target"].read_text()
    assert workspace["queue"].list_items() == []
