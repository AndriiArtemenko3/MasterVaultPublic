from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from mastervault.config import Settings, load_settings
from mastervault.evals.provenance import (
    EVAL_PROMPT_NAMESPACES,
    collect_reproducibility_metadata,
    embedding_runtime_identity,
    provenance_comparison,
    stable_metadata_projection,
)
from mastervault.providers.embedding import MockEmbedding

EXPECTED_EVAL_PROMPT_PATHS = {
    "src/mastervault/prompts/claim_extraction/v1.md",
    "src/mastervault/prompts/contradiction_judge/v1.md",
    "src/mastervault/prompts/corpus_check/v1.md",
    "src/mastervault/prompts/grounded_synthesis/v1.md",
    "src/mastervault/prompts/page_grounded_claim_extraction/v1.md",
    "src/mastervault/prompts/sufficiency_judge/v1.md",
    "src/mastervault/prompts/wiki_draft/v1.md",
}


def test_eval_prompt_manifest_is_exact_and_excludes_change_control_prompts():
    repo_root = Path(__file__).resolve().parents[3]

    metadata = collect_reproducibility_metadata(repo_root, "retrieval", Settings())

    prompt_paths = {row["path"] for row in metadata["prompts"]["files"]}
    assert set(EVAL_PROMPT_NAMESPACES) == {
        path.split("/")[-2] for path in EXPECTED_EVAL_PROMPT_PATHS
    }
    assert prompt_paths == EXPECTED_EVAL_PROMPT_PATHS
    assert metadata["prompts"]["file_count"] == 7
    assert not any("generic_grounded_claim_extraction_v2" in path for path in prompt_paths)
    assert not any("synchronous_change_inference" in path for path in prompt_paths)


def test_bound_eval_prompt_hashes_remain_material_to_compatibility():
    repo_root = Path(__file__).resolve().parents[3]
    metadata = collect_reproducibility_metadata(repo_root, "ask", Settings())
    assert all(len(row["sha256"]) == 64 for row in metadata["prompts"]["files"])
    changed = deepcopy(metadata)
    changed["prompts"]["files"][0]["sha256"] = "0" * 64

    assert provenance_comparison(changed, metadata)["compatible"] is False


def test_machine_and_git_worktree_state_are_diagnostic_not_comparator_inputs():
    metadata = {
        "schema_version": 1,
        "evaluation": "retrieval",
        "source": {
            "git_sha": "a" * 40,
            "git_dirty": False,
            "tree": {"sha256": "b" * 64, "files": []},
            "package_version": "0.2.0",
        },
        "dataset": {"ledger_sha256": "c" * 64},
        "dependencies": {"lock_sha256": "d" * 64},
        "config": {"sha256": "e" * 64},
        "prompts": {"sha256": "f" * 64},
        "storage_schema": {"version": 2},
        "models": {"embedding_model": "example"},
        "environment": {"platform": "one-machine", "python": "3.12.0"},
        "reproduction": {"command": "uv run mvault eval"},
    }
    other_machine = deepcopy(metadata)
    other_machine["source"]["git_sha"] = "9" * 40
    other_machine["source"]["git_dirty"] = True
    other_machine["environment"] = {"platform": "another-machine", "python": "3.12.9"}

    assert stable_metadata_projection(metadata) == stable_metadata_projection(other_machine)


def test_source_tree_identity_is_immutable_diagnostic_provenance_not_compatibility():
    first = {"source": {"tree": {"sha256": "a" * 64}, "package_version": "0.2.0"}}
    second = deepcopy(first)
    second["source"]["tree"]["sha256"] = "b" * 64

    assert stable_metadata_projection(first) == stable_metadata_projection(second)


def test_config_source_filename_is_diagnostic_when_content_and_resolution_match():
    first = {
        "config": {
            "source": {"descriptor": "external:first.toml", "sha256": "a" * 64},
            "resolved": {"storage": {"effective_backend": "sqlite"}},
        }
    }
    second = deepcopy(first)
    second["config"]["source"]["descriptor"] = "external:renamed.toml"

    assert stable_metadata_projection(first) == stable_metadata_projection(second)


def test_custom_ask_case_file_is_the_fingerprinted_eval_input(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    custom = tmp_path / "custom-ask-cases.yaml"
    custom.write_text("- id: custom-case\n", encoding="utf-8")

    metadata = collect_reproducibility_metadata(
        repo_root,
        "ask",
        Settings(),
        evaluation_input=custom,
    )

    assert metadata["dataset"]["evaluation_input"] == {
        "path": "external:custom-ask-cases.yaml",
        "sha256": hashlib.sha256(custom.read_bytes()).hexdigest(),
    }


def test_runtime_identity_is_nonempty_and_material_to_compatibility():
    repo_root = Path(__file__).resolve().parents[3]
    identity = embedding_runtime_identity(MockEmbedding())
    assert identity["provider"] == "mock"
    assert identity["dimensions"] == 384
    assert len(identity["artifact"]["implementation_sha256"]) == 64

    first = collect_reproducibility_metadata(
        repo_root,
        "retrieval",
        Settings(),
        model_identity=identity,
        effective_backend="sqlite",
    )
    changed_identity = deepcopy(identity)
    changed_identity["artifact"]["implementation_sha256"] = "0" * 64
    changed = collect_reproducibility_metadata(
        repo_root,
        "retrieval",
        Settings(),
        model_identity=changed_identity,
        effective_backend="sqlite",
    )

    assert provenance_comparison(changed, first)["compatible"] is False


def test_actual_mv_config_source_and_resolved_values_are_bound_without_secrets(
    tmp_path, monkeypatch
):
    repo_root = Path(__file__).resolve().parents[3]
    alternate = tmp_path / "alternate-mastervault.toml"
    alternate.write_text(
        "[storage]\nbackend = \"sqlite\"\n"
        "[embedding]\nprovider = \"mock\"\nmodel = \"fixture-model\"\n"
        "batch_size = 7\ncost_cap_usd = 0.5\n"
        "[retrieval]\nk = 3\n"
        "[llm]\nprovider = \"openai\"\n"
        "base_url = \"https://alice:password@example.test/private?api_key=hidden\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MV_CONFIG", str(alternate))
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key-not-a-secret")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://fixture:fixture-password@example.test/database"
    )
    settings = load_settings()
    identity = embedding_runtime_identity(MockEmbedding())

    metadata = collect_reproducibility_metadata(
        repo_root,
        "retrieval",
        settings,
        model_identity=identity,
        effective_backend="sqlite",
    )

    assert metadata["config"]["source"] == {
        "descriptor": "external:alternate-mastervault.toml",
        "sha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
    }
    resolved = metadata["config"]["resolved"]
    assert resolved["retrieval"]["k"] == 3
    assert resolved["embedding"]["batch_size"] == 7
    assert resolved["llm"]["endpoint"] == "https://example.test"
    encoded = str(metadata)
    assert "fixture-api-key-not-a-secret" not in encoded
    assert "password" not in encoded
    assert "postgresql://" not in encoded
    assert "api_key" not in encoded


def test_effective_storage_backend_changes_stable_compatibility():
    repo_root = Path(__file__).resolve().parents[3]
    settings = Settings()
    identity = embedding_runtime_identity(MockEmbedding())
    sqlite = collect_reproducibility_metadata(
        repo_root,
        "retrieval",
        settings,
        model_identity=identity,
        effective_backend="sqlite",
    )
    postgres = collect_reproducibility_metadata(
        repo_root,
        "retrieval",
        settings,
        model_identity=identity,
        effective_backend="postgres",
    )

    assert stable_metadata_projection(sqlite) != stable_metadata_projection(postgres)
