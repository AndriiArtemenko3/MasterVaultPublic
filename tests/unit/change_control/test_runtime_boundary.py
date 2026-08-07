from __future__ import annotations

import ast
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
