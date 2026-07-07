"""Shared fixtures for provider unit tests.

Everything here is hermetic: no network, no real SDK clients, no reliance on
the repo's mastervault.toml (MV_CONFIG is pointed at an empty file so tests
control every knob through Settings init kwargs, which take precedence).
Recording doubles live in provider_doubles.py.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from mastervault.config import Settings


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Factory for Settings isolated from the repo TOML and MV_* env vars."""
    empty_toml = tmp_path / "empty-mastervault.toml"
    empty_toml.write_text("")

    def _make(**sections: Any) -> Settings:
        monkeypatch.setenv("MV_CONFIG", str(empty_toml))
        for var in list(os.environ):
            if var.startswith("MV_") and var != "MV_CONFIG":
                monkeypatch.delenv(var, raising=False)
        return Settings(**sections)

    return _make
