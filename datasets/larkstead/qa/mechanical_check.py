#!/usr/bin/env python3
"""Mechanical consistency checker for the Larkstead raw corpus.

Loads bible/company.yaml + bible/storylines/*.yaml as the ground truth,
scans every file under raw/, and writes one JSON line per finding to
qa/violations.jsonl. Read-only: never edits corpus files.

Run from the datasets/larkstead directory or anywhere -- paths are
resolved relative to this script's own location.

    uv run python datasets/larkstead/qa/mechanical_check.py

Checks (see run_all_checks for the numbered list):
  1  SKU tokens resolve to a real SKU in company.yaml (family-prefix
     references like "LS-DSK-001" for the whole desk line are allowed).
  2  Staff full-name bigrams ("Firstname Lastname") match a real staff
     member; first-name-only mentions are never inspected.
  3  VEN-NN tokens resolve to a real vendor.
  4  Every id-shaped token (sku/ticket/invoice/customer_order/vendor/
     production_lot/crm_opportunity) that loosely looks like its type
     also satisfies the strict grammar in company.yaml id_grammars.
  5  banned_strings never appear (case-insensitive, word-boundary).
  6  A "$amount" within 40 chars of a real SKU token must match that
     SKU's price at (or near) a date found in the same file. Heuristic,
     minor severity, flags only clear mismatches.
  7  Invoice-shaped tables: qty * unit price == line total; sum(line
     totals) == subtotal; subtotal - discount + shipping + tax == total.
     Blocker on any mismatch >= $0.02.
  8  Ticket / chat-log timestamps are monotonically non-decreasing
     within a file.
  9  Every doc_id produced by every storyline beat has a matching file
     under raw/ (doc_id must appear as a substring of the filename stem).
 10  No HD-/INV- id is the *primary* subject of two or more different
     files (a real ticket/invoice cannot have two owners), unless the
     id is explicitly declared as shared in a storyline's entities.ids.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
LARKSTEAD_DIR = SCRIPT_DIR.parent
BIBLE_DIR = LARKSTEAD_DIR / "bible"
COMPANY_YAML = BIBLE_DIR / "company.yaml"
STORYLINES_DIR = BIBLE_DIR / "storylines"
RAW_DIR = LARKSTEAD_DIR / "raw"
OUT_PATH = SCRIPT_DIR / "violations.jsonl"

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


# --------------------------------------------------------------------------
# Loading the bible
# --------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_storylines() -> list[dict]:
    out = []
    for path in sorted(STORYLINES_DIR.glob("*.yaml")):
        d = load_yaml(path)
        d["_path"] = str(path)
        out.append(d)
    return out


class Bible:
    """Reference data derived from company.yaml + storylines, read-only."""

    def __init__(self, company: dict, storylines: list[dict]):
        self.company = company
        self.storylines = storylines

        self.skus = {s["sku"]: s for s in company["skus"]}
        # A "family prefix" is the SKU minus its color/size suffix, e.g.
        # LS-DSK-001-48 -> LS-DSK-001. Writers legitimately refer to the
        # whole product line this way ("the LS-DSK-001 desk carton line").
        self.sku_families = set()
        for sku in self.skus:
            parts = sku.split("-")
            if len(parts) == 4:
                self.sku_families.add("-".join(parts[:3]))

        self.staff = {s["name"]: s for s in company["staff"]}
        self.staff_first_to_full: dict[str, set[str]] = defaultdict(set)
        for name in self.staff:
            self.staff_first_to_full[name.split()[0]].add(name)

        self.vendors = {v["vendor_id"]: v for v in company["vendors"]}

        self.banned_strings = list(company["banned_strings"])

        self.id_grammars = company["id_grammars"]

        self.tax_table = company["tax_table"]

        # Union of every storyline's explicitly-allocated shared ids.
        self.shared_allowed_ids: set[str] = set()
        for sl in storylines:
            ids = sl.get("entities", {}).get("ids", {}) or {}
            for key in ("tickets", "invoices"):
                for i in ids.get(key, []) or []:
                    self.shared_allowed_ids.add(i)

    def price_at(self, sku: str, date_str: str | None):
        """Return the price in effect for `sku` at `date_str` (or the
        earliest known price if `date_str` predates the whole history,
        or the sole/first entry if no date is available)."""
        history = sorted(
            self.skus[sku]["price_history"], key=lambda e: e["effective"]
        )
        if not history:
            return None
        if date_str is None:
            return history[0]["price"]
        chosen = history[0]["price"]
        for entry in history:
            if entry["effective"] <= date_str:
                chosen = entry["price"]
            else:
                break
        return chosen

    def all_prices(self, sku: str) -> set[float]:
        return {e["price"] for e in self.skus[sku]["price_history"]}


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------

def load_raw_files() -> list[Path]:
    return sorted(RAW_DIR.rglob("*.md"))


def relpath(p: Path) -> str:
    return str(p.relative_to(LARKSTEAD_DIR))


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def finding(file_: Path | str, check: str, severity: str, detail: str) -> dict:
    fpath = relpath(file_) if isinstance(file_, Path) else file_
    return {"file": fpath, "check": check, "severity": severity, "detail": detail}


# --------------------------------------------------------------------------
# Loose (candidate) id patterns -- deliberately wider than the strict
# grammar in company.yaml id_grammars, so check 4 can catch near-misses
# (wrong digit counts, wrong case, missing padding, ...).
# --------------------------------------------------------------------------

LOOSE_ID_PATTERNS = {
    "sku": re.compile(r"\bLS-[A-Z]{2,5}-[0-9]{1,4}(?:-[A-Z0-9]{1,4})?\b"),
    "ticket": re.compile(r"\bHD-[0-9]{1,6}-[0-9]{1,6}\b"),
    "invoice": re.compile(r"\bINV-[A-Z]{2,5}-[0-9]{1,6}-[0-9]{1,6}\b"),
    "customer_order": re.compile(r"#LS[0-9]{3,7}\b"),
    "vendor": re.compile(r"\bVEN-[0-9]{1,4}\b"),
    "production_lot": re.compile(r"\bLOT-[0-9]{1,6}-[0-9]{1,6}\b"),
    "crm_opportunity": re.compile(r"\bPW-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b"),
}

# Strict SKU / ticket / vendor patterns used directly by checks 1-3 (built
# from the same authoritative grammar, not re-derived from LOOSE_*).
STRICT_SKU_RE = None  # set in main() from bible.id_grammars
STRICT_TICKET_RE = None
STRICT_INVOICE_RE = None
STRICT_VENDOR_RE = None

BIGRAM_RE = re.compile(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b")
DOLLAR_RE = re.compile(r"\$[\d,]+\.?\d{0,2}")


# --------------------------------------------------------------------------
# Check 1 -- SKU existence
# --------------------------------------------------------------------------

def check_sku_exists(path: Path, text: str, bible: Bible):
    for m in STRICT_SKU_RE.finditer(text):
        tok = m.group(0)
        if tok in bible.skus or tok in bible.sku_families:
            continue
        yield finding(
            path, "sku_exists", "blocker",
            f"SKU token '{tok}' does not match any SKU or product family in company.yaml",
        )


# --------------------------------------------------------------------------
# Check 2 -- staff full name
# --------------------------------------------------------------------------

def check_staff_name(path: Path, text: str, bible: Bible):
    for m in BIGRAM_RE.finditer(text):
        first, last = m.group(1), m.group(2)
        candidates = bible.staff_first_to_full.get(first)
        if not candidates:
            continue
        full = f"{first} {last}"
        if full in candidates:
            continue
        yield finding(
            path, "staff_name", "minor",
            f"'{full}' pairs staff first name '{first}' with an unrecognized "
            f"surname; known staff with that first name: {sorted(candidates)}",
        )


# --------------------------------------------------------------------------
# Check 3 -- vendor id existence
# --------------------------------------------------------------------------

def check_vendor_exists(path: Path, text: str, bible: Bible):
    for m in STRICT_VENDOR_RE.finditer(text):
        tok = m.group(0)
        if tok not in bible.vendors:
            yield finding(
                path, "vendor_exists", "blocker",
                f"Vendor token '{tok}' is not a defined vendor_id in company.yaml",
            )


# --------------------------------------------------------------------------
# Check 4 -- id grammar violations
# --------------------------------------------------------------------------

def check_id_grammar(path: Path, text: str, bible: Bible):
    for kind, loose_re in LOOSE_ID_PATTERNS.items():
        strict_pattern = bible.id_grammars[kind]["regex"]
        for m in loose_re.finditer(text):
            tok = m.group(0)
            if re.fullmatch(strict_pattern, tok):
                continue
            yield finding(
                path, "id_grammar", "blocker",
                f"Token '{tok}' looks like a {kind} id but fails the strict "
                f"grammar {strict_pattern!r}",
            )


# --------------------------------------------------------------------------
# Check 5 -- banned real-trademark strings
# --------------------------------------------------------------------------

def _compile_banned(bible: Bible):
    return [
        (s, re.compile(r"\b" + re.escape(s) + r"\b", re.IGNORECASE))
        for s in bible.banned_strings
    ]


def check_banned_strings(path: Path, text: str, bible: Bible, compiled_banned):
    for raw, pat in compiled_banned:
        m = pat.search(text)
        if m:
            yield finding(
                path, "banned_trademark", "blocker",
                f"Banned string '{raw}' found as '{m.group(0)}'",
            )


# --------------------------------------------------------------------------
# Check 6 -- price plausibility near a SKU mention
# --------------------------------------------------------------------------

def _nearest_date(text: str, pos: int) -> str | None:
    best, best_dist = None, None
    for m in DATE_RE.finditer(text):
        dist = abs(m.start() - pos)
        if best_dist is None or dist < best_dist:
            best, best_dist = m.group(1), dist
    return best


def check_price_mismatch(path: Path, text: str, bible: Bible):
    dollars = list(DOLLAR_RE.finditer(text))
    if not dollars:
        return
    skus = [m for m in STRICT_SKU_RE.finditer(text) if m.group(0) in bible.skus]
    if not skus:
        return
    seen = set()
    for d in dollars:
        for s in skus:
            gap = max(d.start(), s.start()) - min(d.end(), s.end())
            gap = max(gap, 0)
            if gap > 40:
                continue
            sku = s.group(0)
            key = (d.start(), sku)
            if key in seen:
                continue
            seen.add(key)
            amount_str = d.group(0).lstrip("$").replace(",", "")
            try:
                amount = round(float(amount_str), 2)
            except ValueError:
                continue
            mid = (d.start() + s.start()) // 2
            date_str = _nearest_date(text, mid)
            expected = bible.price_at(sku, date_str)
            all_known = bible.all_prices(sku)
            if expected is not None and abs(amount - expected) < 0.01:
                continue
            if amount in all_known:
                # matches a price the SKU has carried at some point --
                # plausibly just an off-by-a-few-months date reference.
                continue
            yield finding(
                path, "price_mismatch", "minor",
                f"'{d.group(0)}' near {sku} does not match its price history "
                f"(expected {expected} near date {date_str}; known prices {sorted(all_known)})",
            )


# --------------------------------------------------------------------------
# Check 7 -- invoice arithmetic
# --------------------------------------------------------------------------

TABLE_HEADER = "| qty | item | description | unit price | line total |"
TABLE_ROW_RE = re.compile(
    r"^\|\s*([\d.]+)\s*\|[^|]*\|[^|]*\|\s*([\d,]+\.\d{2})\s*\|\s*([\d,]+\.\d{2})\s*\|\s*$",
    re.M,
)
SUMMARY_LABEL_RE = re.compile(r"^(Subtotal|Discount|Shipping|Tax|Total due)\b")
TRAILING_NUM_RE = re.compile(r"(-?[\d,]+\.\d{2})\s*$")


def _parse_summary_block(text: str) -> dict[str, float] | None:
    lines = text.splitlines()
    label_lines = [(i, m.group(1)) for i, ln in enumerate(lines)
                   for m in [SUMMARY_LABEL_RE.match(ln)] if m]
    if len(label_lines) < 5:
        return None
    vals: dict[str, float] = {}
    for j, (i, label) in enumerate(label_lines):
        end = label_lines[j + 1][0] if j + 1 < len(label_lines) else len(lines)
        val = None
        for k in range(i, end):
            m = TRAILING_NUM_RE.search(lines[k])
            if m:
                val = float(m.group(1).replace(",", ""))
        if val is None:
            return None
        vals[label] = val
    return vals


def check_invoice_arithmetic(path: Path, text: str, bible: Bible):
    if TABLE_HEADER not in text:
        return
    rows = TABLE_ROW_RE.findall(text)
    line_sum = 0.0
    for qty, unit, line in rows:
        qty_f = float(qty)
        unit_f = float(unit.replace(",", ""))
        line_f = float(line.replace(",", ""))
        line_sum += line_f
        if abs(qty_f * unit_f - line_f) >= 0.02:
            yield finding(
                path, "invoice_arithmetic", "blocker",
                f"Line item qty {qty} x unit {unit} = {qty_f * unit_f:.2f}, "
                f"but line total printed as {line}",
            )
    vals = _parse_summary_block(text)
    if vals is None:
        return
    if abs(line_sum - vals["Subtotal"]) >= 0.02:
        yield finding(
            path, "invoice_arithmetic", "blocker",
            f"Sum of line totals {line_sum:.2f} != printed Subtotal {vals['Subtotal']:.2f}",
        )
    calc_total = vals["Subtotal"] - vals["Discount"] + vals["Shipping"] + vals["Tax"]
    if abs(calc_total - vals["Total due"]) >= 0.02:
        yield finding(
            path, "invoice_arithmetic", "blocker",
            f"Subtotal {vals['Subtotal']:.2f} - Discount {vals['Discount']:.2f} + "
            f"Shipping {vals['Shipping']:.2f} + Tax {vals['Tax']:.2f} = "
            f"{calc_total:.2f}, but printed Total due is {vals['Total due']:.2f}",
        )


# --------------------------------------------------------------------------
# Check 8 -- ticket / chat-log timestamp monotonicity
# --------------------------------------------------------------------------

TICKET_TS_RE = re.compile(
    r"---.*?---\s*(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*PT"
)
CHAT_DATE_HEADER_RE = re.compile(r"^Date:\s*(\d{4}-\d{2}-\d{2})", re.M)
CHAT_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})\]")


def check_timestamps_ticket(path: Path, text: str):
    stamps = []
    for m in TICKET_TS_RE.finditer(text):
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        stamps.append((dt, m.group(0)))
    for i in range(1, len(stamps)):
        if stamps[i][0] < stamps[i - 1][0]:
            yield finding(
                path, "timestamp_monotonic", "blocker",
                f"Timestamp goes backwards: '{stamps[i - 1][1].strip()}' "
                f"then '{stamps[i][1].strip()}'",
            )


def check_timestamps_chat(path: Path, text: str):
    dm = CHAT_DATE_HEADER_RE.search(text)
    if not dm:
        return
    date_str = dm.group(1)
    stamps = []
    for m in CHAT_TS_RE.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2))
        stamps.append((h * 60 + mi, f"[{m.group(1)}:{m.group(2)}]"))
    for i in range(1, len(stamps)):
        if stamps[i][0] < stamps[i - 1][0]:
            yield finding(
                path, "timestamp_monotonic", "blocker",
                f"Timestamp goes backwards on {date_str}: "
                f"{stamps[i - 1][1]} then {stamps[i][1]}",
            )


# --------------------------------------------------------------------------
# Check 9 -- storyline doc coverage
# --------------------------------------------------------------------------

def check_storyline_coverage(bible: Bible, stems: set[str]):
    for sl in bible.storylines:
        slug = sl.get("storyline", sl["_path"])
        for beat in sl.get("beats", []):
            for doc in beat.get("produces", []):
                doc_id = doc["doc_id"]
                if doc_id in stems:
                    continue
                if any(doc_id in stem for stem in stems):
                    continue
                yield finding(
                    sl["_path"], "storyline_coverage", "blocker",
                    f"[{slug}] beat {beat.get('beat')} doc_id '{doc_id}' has no "
                    f"matching file under raw/ (in-world date {doc.get('in_world_date')})",
                )


# --------------------------------------------------------------------------
# Check 10 -- duplicate primary ticket/invoice ids
# --------------------------------------------------------------------------

TICKET_PRIMARY_RE = re.compile(r"^Ticket:\s*(HD-[0-9]{4}-[0-9]{5})", re.M)
INVOICE_PRIMARY_RE = re.compile(r"^Invoice:\s*(INV-[A-Z]{3}-[0-9]{4}-[0-9]{3})", re.M)


def check_duplicate_ids(bible: Bible, files: list[Path]):
    primary: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in TICKET_PRIMARY_RE.finditer(text):
            primary[m.group(1)].append(path)
        for m in INVOICE_PRIMARY_RE.finditer(text):
            primary[m.group(1)].append(path)
    for id_, owners in primary.items():
        owners = sorted(set(owners))
        if len(owners) < 2:
            continue
        if id_ in bible.shared_allowed_ids:
            continue
        yield finding(
            owners[0], "duplicate_id", "blocker",
            f"Id '{id_}' is the primary subject of {len(owners)} different files "
            f"and is not declared as shared in any storyline: "
            f"{[relpath(p) for p in owners]}",
        )


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def run_all_checks(bible: Bible, files: list[Path]) -> list[dict]:
    findings: list[dict] = []
    compiled_banned = _compile_banned(bible)
    stems = {p.stem for p in files}

    for path in files:
        text = path.read_text(encoding="utf-8")
        doc_type = path.parent.name

        findings.extend(check_sku_exists(path, text, bible))
        findings.extend(check_staff_name(path, text, bible))
        findings.extend(check_vendor_exists(path, text, bible))
        findings.extend(check_id_grammar(path, text, bible))
        findings.extend(check_banned_strings(path, text, bible, compiled_banned))
        findings.extend(check_price_mismatch(path, text, bible))
        findings.extend(check_invoice_arithmetic(path, text, bible))
        if doc_type == "ticket":
            findings.extend(check_timestamps_ticket(path, text))
        elif doc_type == "chat-log":
            findings.extend(check_timestamps_chat(path, text))

    findings.extend(check_storyline_coverage(bible, stems))
    findings.extend(check_duplicate_ids(bible, files))
    return findings


def main() -> int:
    global STRICT_SKU_RE, STRICT_TICKET_RE, STRICT_INVOICE_RE, STRICT_VENDOR_RE

    if not COMPANY_YAML.exists():
        print(f"FATAL: company.yaml not found at {COMPANY_YAML}", file=sys.stderr)
        return 2

    company = load_yaml(COMPANY_YAML)
    storylines = load_storylines()
    bible = Bible(company, storylines)

    STRICT_SKU_RE = re.compile(r"\b" + bible.id_grammars["sku"]["regex"] + r"\b")
    STRICT_TICKET_RE = re.compile(r"\b" + bible.id_grammars["ticket"]["regex"] + r"\b")
    STRICT_INVOICE_RE = re.compile(r"\b" + bible.id_grammars["invoice"]["regex"] + r"\b")
    STRICT_VENDOR_RE = re.compile(r"\b" + bible.id_grammars["vendor"]["regex"] + r"\b")

    files = load_raw_files()
    if not files:
        print(f"FATAL: no .md files found under {RAW_DIR}", file=sys.stderr)
        return 2

    findings = run_all_checks(bible, files)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    # ---- summary -----------------------------------------------------
    by_check = defaultdict(lambda: {"blocker": 0, "minor": 0})
    for f in findings:
        by_check[f["check"]][f["severity"]] += 1

    print(f"Larkstead mechanical check")
    print(f"  company.yaml : {relpath(COMPANY_YAML)}")
    print(f"  storylines   : {len(storylines)}")
    print(f"  raw files    : {len(files)}")
    print(f"  findings     : {len(findings)}")
    print(f"  output       : {relpath(OUT_PATH)}")
    print()
    print(f"{'check':<24} {'blocker':>8} {'minor':>8}")
    total_blocker = total_minor = 0
    for check in sorted(by_check):
        b = by_check[check]["blocker"]
        m = by_check[check]["minor"]
        total_blocker += b
        total_minor += m
        print(f"{check:<24} {b:>8} {m:>8}")
    print(f"{'TOTAL':<24} {total_blocker:>8} {total_minor:>8}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
