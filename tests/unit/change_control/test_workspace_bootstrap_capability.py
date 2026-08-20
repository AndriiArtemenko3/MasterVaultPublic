"""Focused trust-boundary tests for live workspace-bootstrap evidence handles."""

from __future__ import annotations

import copy
import pickle

import pytest

from mastervault.change_control import workspace_bootstrap as workspace_bootstrap_module


class _EvidenceGuard:
    def __init__(self) -> None:
        self.active = True
        self.verifications = 0

    def verify(self) -> None:
        self.verifications += 1
        if not self.active:
            raise ValueError("simulated closed or drifted composite evidence guard")


def test_evidence_verifier_is_guard_minted_live_and_nonserializable() -> None:
    guard = _EvidenceGuard()
    inventory = object()
    aggregate = object()
    attestation = object()

    with pytest.raises(TypeError, match="guard-created only"):
        workspace_bootstrap_module.VerifiedWorkspaceBootstrapEvidenceVerifier(
            _evidence_guard=guard,
            _resolved_inventory=inventory,
            _resolved_aggregate=aggregate,
            _legacy_attestation=attestation,
            _token=object(),
        )

    verifier = workspace_bootstrap_module._mint_verified_workspace_bootstrap_evidence_verifier(
        guard,
        resolved_inventory=inventory,
        resolved_aggregate=aggregate,
        legacy_attestation=attestation,
    )
    verifier.verify()
    assert guard.verifications == 2

    with pytest.raises(TypeError, match="non-serializable"):
        pickle.dumps(verifier)
    with pytest.raises(TypeError, match="non-serializable"):
        copy.copy(verifier)

    guard.active = False
    with pytest.raises(ValueError, match="closed or drifted"):
        verifier.verify()


def test_evidence_verifier_rejects_in_place_verify_shadowing() -> None:
    guard = _EvidenceGuard()
    verifier = workspace_bootstrap_module._mint_verified_workspace_bootstrap_evidence_verifier(
        guard,
        resolved_inventory=object(),
        resolved_aggregate=object(),
        legacy_attestation=object(),
    )

    object.__setattr__(guard, "verify", lambda: None)

    with pytest.raises(TypeError, match="guard graph was substituted"):
        verifier.verify()

    object.__setattr__(
        verifier,
        "_guard_binding",
        workspace_bootstrap_module.canonical_json_bytes(
            workspace_bootstrap_module._live_guard_binding(guard)
        ),
    )
    with pytest.raises(TypeError, match="guard graph was substituted"):
        verifier.verify()


def test_public_evidence_verifier_rejects_arbitrary_guard_owners() -> None:
    with pytest.raises(TypeError, match="exact workspace and legacy-index guards"):
        workspace_bootstrap_module.create_workspace_bootstrap_evidence_verifier(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )
