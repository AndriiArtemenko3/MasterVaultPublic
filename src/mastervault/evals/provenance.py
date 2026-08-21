"""Reproducibility metadata for committed retrieval and ask baselines."""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from mastervault.config import Settings
from mastervault.providers.embedding import EmbeddingProvider
from mastervault.storage.base import SCHEMA_VERSION

EvalKind = Literal["retrieval", "ask"]
METADATA_SCHEMA_VERSION = 2
BASELINE_SCHEMA_VERSION = 2
EVAL_PROMPT_NAMESPACES = (
    "claim_extraction",
    "contradiction_judge",
    "corpus_check",
    "grounded_synthesis",
    "page_grounded_claim_extraction",
    "sufficiency_judge",
    "wiki_draft",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tree_manifest(
    repo_root: Path, paths: list[Path], *, include_files: bool = True
) -> dict[str, Any]:
    rows = [
        {"path": path.relative_to(repo_root).as_posix(), "sha256": _sha256(path)}
        for path in sorted(paths)
        if path.is_file()
    ]
    manifest: dict[str, Any] = {"sha256": _canonical_hash(rows), "file_count": len(rows)}
    if include_files:
        manifest["files"] = rows
    return manifest


def _git_state(repo_root: Path) -> dict[str, Any]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"git_sha": None, "git_dirty": None}
    return {"git_sha": sha, "git_dirty": bool(status.strip())}


def _package_version() -> str:
    try:
        return version("mastervault")
    except PackageNotFoundError:
        return "source-checkout"


def _installed_versions(names: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            continue
    return versions


def _artifact_tree_identity(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_bytes += size
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size": size,
            }
        )
    return {
        "tree_sha256": _canonical_hash(rows),
        "file_count": len(rows),
        "total_bytes": total_bytes,
    }


def embedding_runtime_identity(embedder: EmbeddingProvider) -> dict[str, Any]:
    """Describe the actual provider and resolved model assets, without secrets.

    Local artifact inspection is deliberately non-loading: a model directory is
    fingerprinted only after FastEmbed has resolved it during the evaluation.
    """
    identity: dict[str, Any] = {
        "provider": embedder.name,
        "implementation": f"{type(embedder).__module__}.{type(embedder).__qualname__}",
        "effective_model": embedder.model_version,
        "dimensions": embedder.dimensions,
    }
    if embedder.name == "local":
        identity["runtime_packages"] = _installed_versions(
            ("fastembed", "onnxruntime", "tokenizers", "numpy")
        )
        outer_model = getattr(embedder, "_model", None)
        resolved_model = getattr(outer_model, "model", None)
        model_dir_value = getattr(resolved_model, "_model_dir", None)
        model_dir = Path(model_dir_value).resolve() if model_dir_value else None
        if model_dir is not None and model_dir.is_dir():
            description = getattr(resolved_model, "model_description", None)
            sources = getattr(description, "sources", None)
            revision = model_dir.name if model_dir.parent.name == "snapshots" else None
            identity["artifact"] = {
                "kind": "fastembed-cache-snapshot",
                "source_repository": getattr(sources, "hf", None),
                "snapshot_revision": revision,
                **_artifact_tree_identity(model_dir),
            }
        else:
            identity["artifact"] = {
                "kind": "fastembed-cache-snapshot",
                "state": "not-resolved-by-this-run",
            }
    elif embedder.name == "openai":
        identity["runtime_packages"] = _installed_versions(("openai", "httpx", "numpy"))
        identity["artifact"] = {
            "kind": "remote-provider-model",
            "immutable_revision": "not-exposed-by-provider",
        }
    else:
        implementation_source = inspect.getsource(type(embedder))
        identity["runtime_packages"] = _installed_versions(("numpy",))
        identity["artifact"] = {
            "kind": "in-process-algorithm",
            "implementation_sha256": hashlib.sha256(
                implementation_source.encode("utf-8")
            ).hexdigest(),
        }
    return identity


def _portable_source(repo_root: Path, source: Path | None) -> dict[str, Any]:
    if source is None:
        return {"descriptor": "programmatic/defaults", "sha256": None}
    source = source.resolve()
    try:
        descriptor = source.relative_to(repo_root).as_posix()
    except ValueError:
        descriptor = f"external:{source.name}"
    return {"descriptor": descriptor, "sha256": _sha256(source)}


def _sanitized_endpoint(value: str | None) -> str | None:
    """Retain endpoint routing identity while dropping userinfo, query and path."""
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return "configured-non-url"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}"


def collect_reproducibility_metadata(
    repo_root: Path,
    kind: EvalKind,
    settings: Settings,
    *,
    evaluation_input: Path | None = None,
    model_identity: dict[str, Any] | None = None,
    effective_backend: str | None = None,
    reproduction_command: str | None = None,
) -> dict[str, Any]:
    """Collect source/data/config/model/environment identity for one eval run."""
    repo_root = repo_root.resolve()
    source_files = list((repo_root / "src" / "mastervault").rglob("*.py"))
    source_files += list((repo_root / "src" / "mastervault").rglob("*.md"))
    source_files += list((repo_root / "src" / "mastervault").rglob("*.sql"))
    prompt_root = repo_root / "src" / "mastervault" / "prompts"
    prompt_files = [
        prompt
        for namespace in EVAL_PROMPT_NAMESPACES
        for prompt in (prompt_root / namespace).glob("v*.md")
    ]
    migration_files = list(
        (repo_root / "src" / "mastervault" / "storage" / "migrations").glob("*/*.sql")
    )
    ledger_path = repo_root / "datasets" / "larkstead" / "corpus-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    eval_input = (evaluation_input or (
        repo_root / "datasets" / "larkstead" / "golden" / "queries.yaml"
        if kind == "retrieval"
        else repo_root / "datasets" / "larkstead" / "golden" / "ask_cases.yaml"
    )).resolve()
    try:
        eval_input_display = eval_input.relative_to(repo_root).as_posix()
    except ValueError:
        # The content hash is the compatibility identity. Do not bake a
        # machine-specific temporary/home path into the cross-machine gate.
        eval_input_display = f"external:{eval_input.name}"
    command = reproduction_command or (
        "uv run mvault eval --json"
        if kind == "retrieval"
        else f"uv run mvault ask-eval --cases {eval_input_display} --json"
    )
    prompt_manifest = _tree_manifest(repo_root, prompt_files)
    migration_manifest = _tree_manifest(repo_root, migration_files)
    processed_manifest = _tree_manifest(
        repo_root,
        list((repo_root / "datasets" / "larkstead" / "processed").rglob("*")),
        include_files=False,
    )
    embeddings_manifest = _tree_manifest(
        repo_root,
        list((repo_root / "datasets" / "larkstead" / "embeddings").rglob("*")),
    )
    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "evaluation": kind,
        "source": {
            **_git_state(repo_root),
            "tree": _tree_manifest(repo_root, source_files, include_files=False),
            "package_version": _package_version(),
        },
        "dataset": {
            "name": "larkstead",
            "ledger_sha256": _sha256(ledger_path),
            "entries_sha256": ledger["entries_sha256"],
            "processed": ledger["summary"]["processed"],
            "historical_no_output": ledger["summary"]["historical_no_output"],
            "processed_tree": processed_manifest,
            "embeddings": embeddings_manifest,
            "evaluation_input": {
                "path": eval_input_display,
                "sha256": _sha256(eval_input),
            },
        },
        "dependencies": {
            "lock_path": "uv.lock",
            "lock_sha256": _sha256(repo_root / "uv.lock"),
        },
        "config": {
            "source": _portable_source(repo_root, settings.config_source),
            "resolved": {
                "storage": {
                    "configured_backend": settings.storage.backend,
                    "effective_backend": effective_backend or settings.storage.backend,
                    "schema_version": SCHEMA_VERSION,
                },
                "embedding": {
                    **settings.embedding.model_dump(mode="json"),
                    "effective_provider": (model_identity or {}).get(
                        "provider", settings.embedding.provider
                    ),
                    "effective_model": (model_identity or {}).get(
                        "effective_model", settings.embedding.model
                    ),
                    "effective_dimensions": (model_identity or {}).get("dimensions"),
                },
                "retrieval": settings.retrieval.model_dump(mode="json"),
                "ask": settings.ask.model_dump(mode="json"),
                "ingestion": settings.ingestion.model_dump(mode="json"),
                "reranker": settings.reranker.model_dump(mode="json"),
                "llm": {
                    "provider": settings.llm.provider,
                    "model_small": settings.llm.model_small,
                    "model_medium": settings.llm.model_medium,
                    "model_large": settings.llm.model_large,
                    "endpoint": _sanitized_endpoint(settings.llm.base_url),
                },
                "budgets": settings.budgets.model_dump(mode="json"),
            },
        },
        "prompts": prompt_manifest,
        "storage_schema": {
            "version": SCHEMA_VERSION,
            "migrations": migration_manifest,
        },
        "models": {
            "embedding_provider": settings.embedding.provider,
            "embedding_model": settings.embedding.model,
            "llm_provider": settings.llm.provider,
            "llm_small": settings.llm.model_small,
            "llm_medium": settings.llm.model_medium,
            "llm_large": settings.llm.model_large,
            "reranker": settings.reranker.backend,
            "runtime_identity": model_identity or {},
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "executable": Path(sys.executable).name,
        },
        "reproduction": {
            "setup_commands": [
                "uv sync",
                "uv run mvault init",
                "uv run mvault demo load",
            ],
            "command": command,
        },
    }


def stable_metadata_projection(metadata: dict[str, Any]) -> dict[str, Any]:
    """Material run inputs, excluding source/environment generation provenance.

    Source SHA/tree and platform are retained diagnostically, but making them
    comparison requirements would force old measurements to be rebound to new
    code instead of rerun. Measurement compatibility is about the data,
    configuration, prompts/schema, dependency lock, and model identity.
    """
    config = metadata.get("config")
    compatible_config = config
    if isinstance(config, dict) and isinstance(config.get("source"), dict):
        # The descriptor is retained to explain where a run loaded its TOML,
        # but an external filename is not a material input. Content plus the
        # fully resolved settings are the portable compatibility identity.
        compatible_config = {
            "source_sha256": config["source"].get("sha256"),
            "resolved": config.get("resolved"),
        }
    return {
        "schema_version": metadata.get("schema_version"),
        "evaluation": metadata.get("evaluation"),
        "dataset": metadata.get("dataset"),
        "dependencies": metadata.get("dependencies"),
        "config": compatible_config,
        "prompts": metadata.get("prompts"),
        "storage_schema": metadata.get("storage_schema"),
        "models": metadata.get("models"),
    }


def validate_baseline_document(kind: EvalKind, baseline: dict[str, Any]) -> list[str]:
    """Validate the frozen document envelope before comparing measurements."""
    errors: list[str] = []
    if baseline.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        errors.append(
            f"baseline_schema_version must be {BASELINE_SCHEMA_VERSION}, "
            f"found {baseline.get('baseline_schema_version')!r}"
        )
    if baseline.get("baseline_kind") != kind:
        errors.append(f"baseline_kind must be {kind!r}, found {baseline.get('baseline_kind')!r}")
    if not isinstance(baseline.get("generated"), str):
        errors.append("baseline generated timestamp is missing")
    key = "configs" if kind == "retrieval" else "cases"
    expected_type = dict if kind == "retrieval" else list
    if not isinstance(baseline.get(key), expected_type):
        errors.append(f"baseline {key!r} has the wrong structure")
    if not isinstance(baseline.get("reproducibility"), dict):
        errors.append("baseline reproducibility metadata is missing")
    return errors


def provenance_comparison(
    current: dict[str, Any], committed: dict[str, Any]
) -> dict[str, Any]:
    """Compatibility verdict plus non-gating generation-provenance differences."""
    compatible = stable_metadata_projection(committed) == stable_metadata_projection(current)
    differences: list[str] = []
    for section in ("source", "environment", "reproduction"):
        if committed.get(section) != current.get(section):
            differences.append(section)
    current_source = (current.get("config") or {}).get("source")
    committed_source = (committed.get("config") or {}).get("source")
    if current_source != committed_source:
        differences.append("config_source")
    return {
        "compatible": compatible,
        "incompatible_inputs": [] if compatible else ["material eval inputs differ"],
        "generation_provenance_differences": differences,
    }


def validate_baseline_metadata(
    repo_root: Path,
    kind: EvalKind,
    settings: Settings,
    committed: dict[str, Any],
    *,
    evaluation_input: Path | None = None,
    model_identity: dict[str, Any] | None = None,
    effective_backend: str | None = None,
) -> list[str]:
    current = collect_reproducibility_metadata(
        repo_root,
        kind,
        settings,
        evaluation_input=evaluation_input,
        model_identity=model_identity,
        effective_backend=effective_backend,
    )
    if stable_metadata_projection(committed) != stable_metadata_projection(current):
        return [
            f"{kind} baseline material inputs differ; rerun and freeze the actual evaluation"
        ]
    return []
