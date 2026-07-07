"""`python -m mastervault.ingest.validate` exit codes and summary line."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

CLEAN_CLAIMS = (
    "key_claims:\n"
    "  - id: clean-01\n"
    '    statement: "A perfectly canonical claim statement."\n'
    "    confidence: high\n"
    "    affects: [refund-policy]\n"
)

DIRTY_CLAIMS = (
    "key_claims:\n"
    "  - id: wrong-id-99\n"
    '    statement: "A statement  with sloppy   spacing."\n'
    "    confidence: medium\n"
    "    affects: ['[[Refund Policy]]']\n"
)

HARD_CLAIMS = (
    "key_claims:\n"
    "  - id: bad-01\n"
    '    statement: "A statement long enough to pass."\n'
    "    confidence: certain\n"
    "    affects: []\n"
)


@pytest.fixture
def run_cli(tmp_path):
    """Run the module CLI hermetically: temp CWD, MV_* env stripped."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MV_")}

    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mastervault.ingest.validate", *args],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
            timeout=60,
        )

    return _run


def _write(tmp_path, name, claims, build_note):
    p = tmp_path / name
    p.write_text(build_note(claims), encoding="utf-8")
    return p


def test_all_pass_exits_zero(run_cli, tmp_path, build_note):
    p = _write(tmp_path, "clean.md", CLEAN_CLAIMS, build_note)
    result = run_cli(str(p))
    assert result.returncode == 0
    assert "[validate] scanned 1 | pass=1 fixed=0 dirty=0 hard=0" in result.stderr


def test_dirty_without_fix_exits_one(run_cli, tmp_path, build_note):
    p = _write(tmp_path, "dirty.md", DIRTY_CLAIMS, build_note)
    result = run_cli(str(p))
    assert result.returncode == 1
    assert "dirty=1" in result.stderr
    assert "dirty\t" in result.stdout


def test_dirty_with_fix_exits_zero_then_passes(run_cli, tmp_path, build_note):
    p = _write(tmp_path, "dirty.md", DIRTY_CLAIMS, build_note)
    first = run_cli(str(p), "--fix")
    assert first.returncode == 0
    assert "fixed=1" in first.stderr

    canonical = p.read_text(encoding="utf-8")
    second = run_cli(str(p), "--fix")
    assert second.returncode == 0
    assert "pass=1" in second.stderr
    assert p.read_text(encoding="utf-8") == canonical


def test_hard_fail_exits_one_even_with_fix(run_cli, tmp_path, build_note):
    p = _write(tmp_path, "hard.md", HARD_CLAIMS, build_note)
    result = run_cli(str(p), "--fix")
    assert result.returncode == 1
    assert "hard=1" in result.stderr
    assert "hard_fail\t" in result.stdout


def test_directory_argument_scans_recursively(run_cli, tmp_path, build_note):
    (tmp_path / "sub").mkdir()
    _write(tmp_path, "clean.md", CLEAN_CLAIMS, build_note)
    (tmp_path / "sub" / "hard.md").write_text(build_note(HARD_CLAIMS), encoding="utf-8")
    result = run_cli(str(tmp_path))
    assert result.returncode == 1
    assert "scanned 2 | pass=1 fixed=0 dirty=0 hard=1" in result.stderr


def test_no_args_is_usage_error(run_cli):
    assert run_cli().returncode == 2


def test_nonexistent_path_is_usage_error(run_cli, tmp_path):
    result = run_cli(str(tmp_path / "missing.md"))
    assert result.returncode == 2
