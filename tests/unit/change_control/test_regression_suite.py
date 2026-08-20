from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mastervault.change_control.regression_suite import (
    RegressionSuiteError,
    load_regression_suite,
    parse_regression_suite_bytes,
)


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "suite_id": "returns-v1",
        "suite_version": 1,
        "cases": [
            {
                "case_id": "targeted-return",
                "role": "targeted",
                "kind": "search",
                "query": "What is the return window?",
                "domain": "customer-support",
                "k": 5,
                "record_types": ["claim", "wiki"],
                "rerank": False,
            },
            {
                "case_id": "control-shipping",
                "role": "control",
                "kind": "ask",
                "query": "How long does standard shipping take?",
                "domain": "customer-support",
                "max_rounds": 2,
                "budget_usd_micros": 25000,
            },
        ],
    }


def _bytes(payload: dict[str, object] | None = None) -> bytes:
    return json.dumps(payload or _payload(), separators=(",", ":")).encode()


def test_admission_sorts_cases_and_binds_original_and_canonical_bytes() -> None:
    payload = _payload()
    original_order = parse_regression_suite_bytes(_bytes(payload))
    payload["cases"] = list(reversed(payload["cases"]))  # type: ignore[arg-type]
    admitted = parse_regression_suite_bytes(_bytes(payload))

    assert [case.case_id for case in admitted.suite.cases] == [
        "control-shipping",
        "targeted-return",
    ]
    assert admitted.original_sha256 != admitted.canonical_sha256
    assert admitted.original_sha256 != original_order.original_sha256
    assert admitted.canonical_sha256 == original_order.canonical_sha256
    assert parse_regression_suite_bytes(admitted.suite.canonical_bytes).canonical_sha256 == (
        admitted.canonical_sha256
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf{}",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b"[]",
        b"{",
        b"{} trailing",
    ],
)
def test_parser_rejects_ambiguous_or_non_strict_json(payload: bytes) -> None:
    with pytest.raises(RegressionSuiteError):
        parse_regression_suite_bytes(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("extra",), True),
        (("suite_version",), "1"),
        (("cases", 0, "k"), 2.5),
        (("cases", 0, "rerank"), True),
        (("cases", 0, "expected_hits"), []),
        (("cases", 0, "query"), "  not normalized  "),
    ],
)
def test_parser_rejects_unknown_coerced_answer_shaped_and_unnormalized_values(
    path: tuple[object, ...], value: object
) -> None:
    payload: object = _payload()
    current = payload
    for component in path[:-1]:
        current = current[component]  # type: ignore[index]
    current[path[-1]] = value  # type: ignore[index]
    with pytest.raises(RegressionSuiteError):
        parse_regression_suite_bytes(_bytes(payload))  # type: ignore[arg-type]


def test_parser_rejects_duplicate_cases_limits_and_missing_roles() -> None:
    duplicate = _payload()
    duplicate["cases"] = [duplicate["cases"][0], duplicate["cases"][0]]  # type: ignore[index]
    with pytest.raises(RegressionSuiteError, match="unique"):
        parse_regression_suite_bytes(_bytes(duplicate))

    too_many = _payload()
    template = too_many["cases"][0]  # type: ignore[index]
    too_many["cases"] = [dict(template, case_id=f"case-{index:03d}") for index in range(129)]
    with pytest.raises(RegressionSuiteError):
        parse_regression_suite_bytes(_bytes(too_many))

    targeted_only = _payload()
    targeted_only["cases"] = [targeted_only["cases"][0]]  # type: ignore[index]
    with pytest.raises(RegressionSuiteError, match="control"):
        parse_regression_suite_bytes(_bytes(targeted_only))


def test_file_loader_reads_to_eof_even_when_os_read_returns_short_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "suite.json"
    path.write_bytes(_bytes())
    path.chmod(0o600)
    original = os.read

    def short_read(fd: int, size: int) -> bytes:
        return original(fd, min(size, 3))

    monkeypatch.setattr(os, "read", short_read)
    admitted = load_regression_suite(path)
    assert len(admitted.suite.cases) == 2


def test_file_loader_rejects_symlink_hardlink_fifo_and_oversize(tmp_path: Path) -> None:
    regular = tmp_path / "suite.json"
    regular.write_bytes(_bytes())
    regular.chmod(0o600)

    symlink = tmp_path / "suite-link.json"
    symlink.symlink_to(regular)
    with pytest.raises(RegressionSuiteError):
        load_regression_suite(symlink)

    hardlink = tmp_path / "suite-hard.json"
    os.link(regular, hardlink)
    with pytest.raises(RegressionSuiteError):
        load_regression_suite(regular)

    fifo = tmp_path / "suite-fifo.json"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(RegressionSuiteError):
        load_regression_suite(fifo)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    oversized.chmod(0o600)
    with pytest.raises(RegressionSuiteError):
        load_regression_suite(oversized)


def test_file_loader_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    suite = real_parent / "suite.json"
    suite.write_bytes(_bytes())
    suite.chmod(0o600)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(RegressionSuiteError):
        load_regression_suite(linked_parent / "suite.json")


def test_file_loader_bounds_root_open_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "suite.json"

    def fail_open(*args: object, **kwargs: object) -> int:
        raise OSError("synthetic root failure")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises(RegressionSuiteError, match="cannot be opened safely") as raised:
        load_regression_suite(path)
    assert isinstance(raised.value.__cause__, OSError)


def test_file_loader_closes_parent_descriptors_when_child_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "nested"
    parent.mkdir()
    path = parent / "suite.json"
    path.write_bytes(_bytes())
    path.chmod(0o600)
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(*args: object, **kwargs: object) -> int:
        fd = original_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(fd)
        return fd

    def tracked_close(fd: int) -> None:
        closed.append(fd)
        original_close(fd)

    def fail_child_fstat(fd: int) -> os.stat_result:
        if len(opened) >= 2 and fd == opened[-1]:
            raise OSError("synthetic descriptor failure")
        return original_fstat(fd)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    monkeypatch.setattr(os, "fstat", fail_child_fstat)
    with pytest.raises(RegressionSuiteError, match="cannot be opened safely"):
        load_regression_suite(path)
    assert set(opened) <= set(closed)
