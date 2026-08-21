"""Pinned packaged inference assets for the synchronous lifecycle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import RootModel

from mastervault.change_control.managed_revision_materialization import (
    RevisionPlanningWireResponse,
)
from mastervault.change_control.models import canonical_json_bytes
from mastervault.change_control.recorded_inference import (
    ClassificationWireResponse,
    DependencyWireResponse,
    ImpactWireResponse,
)
from mastervault.prompts.registry import load


class ApplicationInferenceWireResponse(
    RootModel[
        ClassificationWireResponse
        | DependencyWireResponse
        | ImpactWireResponse
        | RevisionPlanningWireResponse
    ]
):
    """Schema union; the recorded layer applies the stricter task-specific validator."""


@dataclass(frozen=True)
class ApplicationInferenceAssetsV1:
    registry_contract_id: str
    contract_id: str
    contract_version: int
    algorithm_manifest_bytes: bytes
    prompt_bytes: bytes
    response_schema_bytes: bytes
    algorithm_manifest_sha256: str
    prompt_sha256: str
    response_schema_sha256: str


def load_application_inference_assets_v1() -> ApplicationInferenceAssetsV1:
    spec = load("synchronous_change_inference", 1)
    if spec.output_model is not ApplicationInferenceWireResponse or spec.tier != "medium":
        raise ValueError("synchronous lifecycle prompt registry identity is invalid")
    algorithm = canonical_json_bytes(
        {
            "algorithm": "synchronous-change-inference",
            "contract_version": 1,
            "tasks": ["classification", "dependency", "impact", "revision-planning"],
            "validation": "recorded-task-specific-v1",
        }
    )
    prompt = spec.body.encode("utf-8")
    schema = canonical_json_bytes(ApplicationInferenceWireResponse.model_json_schema())
    return ApplicationInferenceAssetsV1(
        registry_contract_id=spec.contract_id,
        contract_id="synchronous-change-inference",
        contract_version=spec.version,
        algorithm_manifest_bytes=algorithm,
        prompt_bytes=prompt,
        response_schema_bytes=schema,
        algorithm_manifest_sha256=hashlib.sha256(algorithm).hexdigest(),
        prompt_sha256=hashlib.sha256(prompt).hexdigest(),
        response_schema_sha256=hashlib.sha256(schema).hexdigest(),
    )


__all__ = [
    "ApplicationInferenceAssetsV1",
    "ApplicationInferenceWireResponse",
    "load_application_inference_assets_v1",
]
