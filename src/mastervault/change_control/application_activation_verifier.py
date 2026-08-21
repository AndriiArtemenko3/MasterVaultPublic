"""Read-only verification of one activated public lifecycle run."""

from __future__ import annotations

from collections.abc import Callable

from mastervault.change_control.change_application_contracts import (
    ChangeRunPhaseV1,
    ChangeRunStatusV1,
)
from mastervault.change_control.operator_run import OperatorRunView
from mastervault.change_control.query_generation import (
    QueryGenerationKind,
    QueryGenerationSelector,
    ResolvedQueryGeneration,
)


class ActivatedEvidenceVerificationError(ValueError):
    """Activated authority and exact serving/index evidence do not agree."""


type ActiveQueryGenerationOpener = Callable[[], ResolvedQueryGeneration]


class ReadOnlyActivatedEvidenceVerifier:
    """Open ACTIVE serving evidence, cross-bind it to status, and always close it.

    ``ResolvedQueryGeneration`` owns the full read-only serving reconstruction:
    activation receipt and prior authority, immutable generation repository,
    exact active SQLite index, and final authority verification on close.
    """

    def __init__(self, opener: ActiveQueryGenerationOpener) -> None:
        if not callable(opener):
            raise TypeError("active query-generation opener must be callable")
        self._opener = opener

    def __call__(self, run: OperatorRunView, status: ChangeRunStatusV1) -> None:
        resolved: ResolvedQueryGeneration | None = None
        primary_error: BaseException | None = None
        try:
            resolved = self._opener()
            if type(resolved) is not ResolvedQueryGeneration:
                raise TypeError("active query-generation opener returned substituted resources")
            resolved.verify()
            metadata = resolved.metadata
            activation = status.activation
            current = status.current_authority
            if not (
                status.run_id == run.record.command.run_id
                and status.phase == ChangeRunPhaseV1.ACTIVATED
                and activation is not None
                and metadata.selection.selector == QueryGenerationSelector.ACTIVE
                and metadata.generation_kind == QueryGenerationKind.MANAGED
                and metadata.is_active
                and metadata.generation_id
                == metadata.active_generation_id
                == activation.generation_id
                == current.generation_id
                and metadata.generation_number == current.generation_number == 1
                and metadata.active_authority_revision == current.revision
                and metadata.manifest_sha256 == current.manifest_sha256
            ):
                raise ActivatedEvidenceVerificationError(
                    "activated status differs from exact ACTIVE serving/index evidence"
                )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if resolved is not None:
                try:
                    resolved.close()
                except BaseException as close_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(
                        "activated verification also failed while closing retained resources: "
                        f"{type(close_error).__name__}"
                    )


__all__ = [
    "ActivatedEvidenceVerificationError",
    "ActiveQueryGenerationOpener",
    "ReadOnlyActivatedEvidenceVerifier",
]
