"""Shared builders for vaultfs unit tests. Hermetic: tmp_path only, no network."""

from __future__ import annotations

import pytest

SOURCE_NOTE = """\
---
domain: customer-support
type: source
title: Refund policy call
source_type: call-transcript
tags: [refunds, policy]
status: draft
created: 2026-07-01
updated: 2026-07-01
key_claims:
  - id: refund-note-01
    statement: "Refunds over 500 USD require manager approval."
    confidence: high
    affects: [refund-policy]
  - id: refund-note-02
    statement: "Standard refunds settle within five business days."
    confidence: medium
    affects: [refund-policy, sla]
---

# Refund policy call

First paragraph of the body.

Second paragraph of the body.
"""


@pytest.fixture
def source_note_text() -> str:
    return SOURCE_NOTE


@pytest.fixture
def make_note():
    """The make_note_text builder, exposed as a fixture (avoids conftest imports)."""
    return make_note_text


def make_note_text(
    *,
    domain: str = "operations",
    note_type: str = "wiki",
    title: str = "Test Note",
    extra_yaml: str = "",
    body: str = "Body paragraph.",
) -> str:
    return (
        "---\n"
        f"domain: {domain}\n"
        f"type: {note_type}\n"
        f"title: {title}\n"
        "tags: []\n"
        "status: draft\n"
        "created: 2026-07-01\n"
        "updated: 2026-07-01\n"
        f"{extra_yaml}"
        "---\n"
        f"\n{body}\n"
    )
