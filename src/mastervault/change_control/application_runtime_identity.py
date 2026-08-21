"""Canonical non-secret runtime identity shared by lifecycle write/read paths."""

from __future__ import annotations

import hashlib

from mastervault.change_control.application_inference_assets import (
    load_application_inference_assets_v1,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.config import Settings


def application_configuration_sha256(settings: Settings) -> str:
    """Bind inference/retrieval semantics without serializing credentials or paths."""

    assets = load_application_inference_assets_v1()
    payload = {
        "namespace": "mastervault.synchronous-change-runtime.v1",
        "storage_backend": settings.storage.backend,
        "embedding_provider": settings.embedding.provider,
        "embedding_model": settings.embedding.model,
        "embedding_batch_size": settings.embedding.batch_size,
        "llm_provider": settings.llm.provider,
        "llm_model_small": settings.llm.model_small,
        "llm_model_medium": settings.llm.model_medium,
        "llm_model_large": settings.llm.model_large,
        "retrieval": settings.retrieval.model_dump(mode="json"),
        "contract_id": assets.contract_id,
        "contract_version": assets.contract_version,
        "algorithm_manifest_sha256": assets.algorithm_manifest_sha256,
        "prompt_sha256": assets.prompt_sha256,
        "response_schema_sha256": assets.response_schema_sha256,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


__all__ = ["application_configuration_sha256"]
