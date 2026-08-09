from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src/mastervault/change_control"
MANIFEST = (
    Path(__file__).resolve().parents[3] / "datasets/larkstead/change_control/sl2_prechange.yaml"
)


def test_production_package_has_no_evaluator_import_or_gold_path_literal():
    forbidden_import = "mastervault.evals"
    forbidden_path_fragments = (
        "datasets/larkstead/golden",
        "change_impact.yaml",
    )
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported: list[str] = []
        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
        assert not any(
            name == forbidden_import or name.startswith(f"{forbidden_import}.") for name in imported
        )
        assert not any(
            fragment in value for fragment in forbidden_path_fragments for value in strings
        )


def test_runtime_manifest_does_not_encode_gold_keys_or_labels():
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    forbidden_keys = {
        "classification",
        "dependencies",
        "edge_label",
        "expected_affected_document_ids",
        "expected_impacts",
        "expected_pair_classifications",
        "expected_review_decision",
        "patches",
        "temporal_phases",
    }
    forbidden_values = {
        "SUPERSEDES",
        "CONTRADICTS",
        "COEXISTS",
        "UNRELATED",
        "DEPENDS_ON",
        "approve",
        "edit",
        "reject",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert not (set(value) & forbidden_keys)
            assert not any(str(key).startswith("expected_") for key in value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            assert value not in forbidden_values

    walk(data)


def test_public_impact_type_hints_resolve_in_a_fresh_interpreter() -> None:
    source_root = PACKAGE_ROOT.parents[1]
    script = f"""
import sys
from typing import get_type_hints

sys.path.insert(0, {str(source_root)!r})

from mastervault.change_control.impact_analysis import (
    build_impact_workload,
    validate_impact_workload,
)
from mastervault.change_control.impact_results import validate_impact_results
from mastervault.change_control.review import HumanReviewDecision, HumanReviewRequest
from mastervault.change_control.reviewed_snapshot import ReviewedTemporalSnapshotAuthority
from mastervault.change_control.reviewed_snapshot_binding import ReviewedTemporalSnapshotBinding
from mastervault.change_control.temporal_analysis import TemporalAnalysisEvidence
from mastervault.change_control.temporal_proposal import TemporalProposalCommit

binding_hints = get_type_hints(ReviewedTemporalSnapshotBinding.create)
assert binding_hints["temporal_analysis"] is TemporalAnalysisEvidence
assert binding_hints["commit"] is TemporalProposalCommit
assert binding_hints["request"] is HumanReviewRequest
assert binding_hints["decision"] is HumanReviewDecision

assert get_type_hints(build_impact_workload)["authority"] is ReviewedTemporalSnapshotAuthority
assert get_type_hints(validate_impact_workload)["authority"] is ReviewedTemporalSnapshotAuthority
assert get_type_hints(validate_impact_results)["authority"] is ReviewedTemporalSnapshotAuthority
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
