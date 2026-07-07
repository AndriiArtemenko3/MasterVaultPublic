"""Claim schema gate for `key_claims:` frontmatter blocks.

LLM extractors are reliable on claim semantics but variable on mechanical
shape (id naming, affects slugs, stray whitespace). This gate splits findings
into two axes:

- AUTOFIX (mechanical, idempotent, applied only with fix=True, written via
  `surgical_replace_field` on the `key_claims:` field only): renumber ids to
  `<file-slug>-NN`, normalize `affects:` entries to deduped kebab-case bare
  slugs, collapse whitespace inside statements.
- HARD-FAIL (never auto-fixed — intent is unrecoverable from shape): empty or
  under-8-char or over-40-word statements, confidence outside low/medium/high,
  claim count over `settings.ingestion.max_claims_per_doc`, unparseable
  frontmatter, duplicate statements within one note.

Module CLI: `python -m mastervault.ingest.validate <paths...> [--fix]`.
Exit 0 when everything passes (or was fixed), 1 on any hard_fail or on dirty
files without --fix, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from mastervault.config import Settings, load_settings
from mastervault.models import WIKI_SLUG_RE, Confidence
from mastervault.vaultfs.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    surgical_replace_field,
)
from mastervault.vaultfs.notes import slugify

MIN_STATEMENT_CHARS = 8
MAX_STATEMENT_WORDS = 40

Status = Literal["pass", "fixed", "dirty", "hard_fail"]

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class Report:
    path: Path
    status: Status
    autofixes: list[str] = field(default_factory=list)
    hard_fails: list[str] = field(default_factory=list)


# --- canonical rendering ----------------------------------------------------


class _Quoted(str):
    pass


class _Flow(list):
    pass


class _Dumper(yaml.SafeDumper):
    """Local dumper so representers don't leak into global yaml state."""


_Dumper.add_representer(
    _Quoted,
    lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"'),
)
_Dumper.add_representer(
    _Flow,
    lambda dumper, data: dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True),
)


def render_key_claims(claims: list[dict]) -> str:
    """Deterministic YAML for the `key_claims:` block: block list, double-quoted
    statements, flow-style `affects:`. Unknown per-claim keys are carried
    through after the canonical four."""
    rendered = []
    for c in claims:
        record: dict = {
            "id": c["id"],
            "statement": _Quoted(c["statement"]),
            "confidence": c["confidence"],
            "affects": _Flow(c.get("affects") or []),
        }
        for k, v in c.items():
            if k not in record:
                record[k] = v
        rendered.append(record)
    return yaml.dump(
        {"key_claims": rendered},
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10_000,
    )


# --- normalization helpers --------------------------------------------------


def _normalize_affects_entry(value: object) -> str | None:
    """One `affects:` entry -> bare kebab-case slug, or None when unrecoverable."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    while s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].strip()
    s = _NON_SLUG_RE.sub("-", s.lower()).strip("-")
    if s and WIKI_SLUG_RE.match(s):
        return s
    return None


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


# --- core gate ---------------------------------------------------------------


def validate_source_note(
    path: Path | str, fix: bool = False, *, settings: Settings | None = None
) -> Report:
    """Validate (and with fix=True, canonicalize) one note's `key_claims:` block.

    Notes without frontmatter `key_claims:` (or with an empty list) pass —
    they are out of scope for this gate. Hard fails always suppress the write,
    even under fix=True.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    try:
        data, _body = parse_frontmatter(text)
    except FrontmatterError as exc:
        return Report(path, "hard_fail", hard_fails=[f"unparseable frontmatter: {exc}"])

    raw_claims = data.get("key_claims")
    if not raw_claims:
        return Report(path, "pass")
    if not isinstance(raw_claims, list):
        return Report(
            path,
            "hard_fail",
            hard_fails=[f"key_claims is {type(raw_claims).__name__}, expected list"],
        )

    if settings is None:
        settings = load_settings()
    max_claims = settings.ingestion.max_claims_per_doc

    autofixes: list[str] = []
    hard_fails: list[str] = []
    canonical: list[dict] = []
    seen_statements: dict[str, int] = {}

    if len(raw_claims) > max_claims:
        hard_fails.append(f"too many claims: {len(raw_claims)} > {max_claims}")

    file_slug = slugify(path.stem)

    for idx, claim in enumerate(raw_claims, start=1):
        if not isinstance(claim, dict):
            hard_fails.append(f"claim #{idx} is {type(claim).__name__}, expected mapping")
            continue

        statement = claim.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            hard_fails.append(f"claim #{idx}: statement missing or empty")
            continue
        collapsed = _collapse_ws(statement)
        if collapsed != statement:
            autofixes.append(f"claim #{idx}: collapsed whitespace in statement")
        if len(collapsed) < MIN_STATEMENT_CHARS:
            hard_fails.append(
                f"claim #{idx}: statement shorter than {MIN_STATEMENT_CHARS} chars"
            )
            continue
        n_words = len(collapsed.split())
        if n_words > MAX_STATEMENT_WORDS:
            hard_fails.append(
                f"claim #{idx}: statement has {n_words} words > {MAX_STATEMENT_WORDS}"
            )
            continue
        dupe_key = collapsed.casefold()
        if dupe_key in seen_statements:
            hard_fails.append(
                f"claim #{idx}: duplicate statement (same as claim #{seen_statements[dupe_key]})"
            )
            continue
        seen_statements[dupe_key] = idx

        confidence = claim.get("confidence")
        if confidence not in {c.value for c in Confidence}:
            hard_fails.append(
                f"claim #{idx}: confidence={confidence!r} not in {{low, medium, high}}"
            )
            continue

        canonical_id = f"{file_slug}-{len(canonical) + 1:02d}"
        if claim.get("id") != canonical_id:
            autofixes.append(f"claim #{idx}: id {claim.get('id')!r} -> {canonical_id!r}")

        raw_affects = claim.get("affects") or []
        if not isinstance(raw_affects, list):
            raw_affects = [raw_affects]
        normalized: list[str] = []
        for entry in raw_affects:
            slug = _normalize_affects_entry(entry)
            if slug is None:
                autofixes.append(f"claim #{idx}: dropped unusable affects entry {entry!r}")
            elif slug not in normalized:
                normalized.append(slug)
        if normalized != claim.get("affects"):
            autofixes.append(f"claim #{idx}: affects {claim.get('affects')!r} -> {normalized!r}")

        record = {
            "id": canonical_id,
            "statement": collapsed,
            "confidence": confidence,
            "affects": normalized,
        }
        for k, v in claim.items():
            if k not in record:
                record[k] = v
        canonical.append(record)

    if hard_fails:
        return Report(path, "hard_fail", autofixes=autofixes, hard_fails=hard_fails)
    if not autofixes:
        return Report(path, "pass")
    if not fix:
        return Report(path, "dirty", autofixes=autofixes)

    new_text = surgical_replace_field(text, "key_claims", render_key_claims(canonical))
    path.write_text(new_text, encoding="utf-8")
    return Report(path, "fixed", autofixes=autofixes)


# --- CLI ----------------------------------------------------------------------


def _collect_files(targets: list[Path]) -> list[Path] | None:
    """Expand files/dirs to a deduped .md list; None on a nonexistent path."""
    out: list[Path] = []
    seen: set[Path] = set()
    for t in targets:
        if t.is_file():
            candidates = [t]
        elif t.is_dir():
            candidates = sorted(t.rglob("*.md"))
        else:
            return None
        for f in candidates:
            if f.suffix != ".md":
                continue
            r = f.resolve()
            if r not in seen:
                seen.add(r)
                out.append(f)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mastervault.ingest.validate",
        description="Validate key_claims frontmatter blocks against the claim schema.",
    )
    parser.add_argument("paths", nargs="+", help="Markdown files or directories.")
    parser.add_argument("--fix", action="store_true", help="Apply mechanical autofixes in place.")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    files = _collect_files([Path(p) for p in args.paths])
    if files is None:
        print("[validate] error: path does not exist", file=sys.stderr)
        return 2

    counts = {"pass": 0, "fixed": 0, "dirty": 0, "hard_fail": 0}
    settings = load_settings()
    for f in files:
        report = validate_source_note(f, fix=args.fix, settings=settings)
        counts[report.status] += 1
        if report.status == "pass":
            continue
        print(f"{report.status}\t{f}")
        for h in report.hard_fails:
            print(f"  hard: {h}")
        for a in report.autofixes:
            print(f"  fix:  {a}")

    print(
        f"[validate] scanned {len(files)} | "
        f"pass={counts['pass']} fixed={counts['fixed']} "
        f"dirty={counts['dirty']} hard={counts['hard_fail']}",
        file=sys.stderr,
    )
    return 1 if counts["hard_fail"] or counts["dirty"] else 0


if __name__ == "__main__":
    sys.exit(main())
