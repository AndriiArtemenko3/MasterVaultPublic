"""Typed exceptions for the orchestration substrate + process exit codes.

Every pipeline-facing failure mode gets its own exception class so callers can
branch on type instead of parsing messages. EXIT_CODES is the single mapping
CLI entrypoints use to turn an outcome into a process exit status.
"""

from __future__ import annotations


class MasterVaultError(Exception):
    """Base class for every orchestration-layer error."""


class UsageError(MasterVaultError):
    """The caller invoked something wrong (bad args, bad state for the verb)."""


class BudgetExceeded(MasterVaultError):
    """A recorded or estimated spend would push the ledger past its cap."""


class ResumeConflict(MasterVaultError):
    """A run dir cannot be resumed (missing plan/events, or plan mismatch)."""


class HardFailError(MasterVaultError):
    """A contract's hard-fail checks still failed after the single retry."""


class UnknownEventError(MasterVaultError):
    """An event name outside the closed enum was written or read."""


class PromptNotFoundError(MasterVaultError):
    """No prompt file exists for the requested contract id / version."""


class PromptInvalidError(MasterVaultError):
    """A prompt file failed load-time validation (header, model, or render)."""


class PatchError(MasterVaultError):
    """A unified diff could not be applied cleanly (hunk mismatch)."""


class UnreadableDocument(MasterVaultError):
    """An input file could not be turned into text.

    Raised instead of letting a parser's own exception escape, so a corrupt PDF
    or a binary file mislabelled `.txt` fails with a bounded, actionable
    message naming the file and what was wrong with it.
    """


# Outcome name -> process exit code. CLI entrypoints exit through this table.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "completed-with-failures": 1,
    "usage": 2,
    "budget-exhausted": 3,
    "resume-conflict": 4,
}

_EXCEPTION_OUTCOMES: dict[type[MasterVaultError], str] = {
    UsageError: "usage",
    BudgetExceeded: "budget-exhausted",
    ResumeConflict: "resume-conflict",
}


def exit_code_for(exc: BaseException) -> int:
    """Map an exception to its process exit code (default: completed-with-failures)."""
    for exc_type, outcome in _EXCEPTION_OUTCOMES.items():
        if isinstance(exc, exc_type):
            return EXIT_CODES[outcome]
    return EXIT_CODES["completed-with-failures"]
