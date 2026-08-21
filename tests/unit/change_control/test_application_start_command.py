from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mastervault.change_control.application_start_command import (
    ApplicationStartCommandConflictError,
    ApplicationStartCommandRepository,
    ApplicationStartCommandV1,
)
from mastervault.change_control.change_application_contracts import ChangeExecutionModeV1
from mastervault.models import Domain


def _command(*, operation_id: str = "start:one", run_suffix: str = "1"):
    return ApplicationStartCommandV1.create(
        operation_id=operation_id,
        run_id=f"operatorrun:{run_suffix * 64}",
        base_authority_id=f"mauthority:{'2' * 64}",
        base_authority_revision=0,
        base_active_pointer_sha256="3" * 64,
        source_sha256="4" * 64,
        source_byte_count=123,
        source_metadata_sha256="5" * 64,
        suite_id="operator-suite",
        suite_version=1,
        suite_original_sha256="6" * 64,
        suite_original_byte_count=456,
        suite_canonical_sha256="7" * 64,
        domain=Domain.OPERATIONS,
        mode=ChangeExecutionModeV1.LIVE,
        replay_bundle_id=None,
        replay_bundle_sha256=None,
        configuration_sha256="8" * 64,
        claimed_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def test_start_owner_exact_retry_run_reopen_and_cross_operation_conflict(tmp_path) -> None:
    repository = ApplicationStartCommandRepository(tmp_path / "evidence")
    command = _command()
    first = repository.claim(command)
    assert repository.claim(command) == first
    assert repository.reopen_operation(command.operation_id) == first
    assert repository.reopen_run(command.run_id) == first

    with pytest.raises(ApplicationStartCommandConflictError):
        repository.claim(_command(operation_id="start:two"))
