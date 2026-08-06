#!/usr/bin/env bash
# Release gate for the built distributions.
#
#   1. build the sdist and the wheel
#   2. scan both for workspaces, caches, secrets, test output and corpus-
#      generation inputs that have no business in a distribution
#   3. install the wheel into a clean, empty virtualenv (no dev extras, no
#      editable install, nothing from the repo on sys.path)
#   4. run a CLI smoke flow from that installed artifact only
#
# Run from the repository root:  ./scripts/check-package.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { printf '\n  FAIL: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------------------
step "build"
# ---------------------------------------------------------------------------
rm -rf dist
uv build
SDIST="$(ls dist/*.tar.gz)"
WHEEL="$(ls dist/*.whl)"
printf '  sdist: %s (%s)\n' "$SDIST" "$(du -h "$SDIST" | cut -f1)"
printf '  wheel: %s (%s)\n' "$WHEEL" "$(du -h "$WHEEL" | cut -f1)"

# ---------------------------------------------------------------------------
step "artifact scan"
# ---------------------------------------------------------------------------
tar tzf "$SDIST" > "$WORK/sdist.txt"
unzip -Z1 "$WHEEL" > "$WORK/wheel.txt"
cat "$WORK/sdist.txt" "$WORK/wheel.txt" > "$WORK/all.txt"

# Anything matching these has no business in a distribution.
FORBIDDEN='(^|/)(\.env|\.env\..*|\.coverage|\.coverage\..*|coverage\.xml|htmlcov|\.pytest_cache|\.ruff_cache|\.mypy_cache|\.uv-cache|__pycache__|vault_workspace|_build|node_modules|\.venv)(/|$)|\.(key|pem|pyc|pyo|so|log)$|junit-.*\.xml$|BUILD_LOG\.md|MORNING_REPORT\.md'
# `.env.example` is a documented, secret-free template and is meant to ship;
# every other .env variant is not.
ALLOWED='(^|/)\.env\.example$'
if grep -Ei "$FORBIDDEN" "$WORK/all.txt" | grep -Ev "$ALLOWED" | grep -E '.'; then
  fail "distribution contains a workspace, cache, secret, or test artifact (listed above)"
fi
printf '  no workspaces, caches, secrets or test output\n'

# The raw corpus and its synthetic bible are intentionally in the sdist
# because the shipped ledger and raw-QA tests require both. The package-only
# wheel is checked separately below and carries neither.
grep -q 'datasets/larkstead/raw/internal-admin/contract/contract-addendum-verdant-qc.md' \
  "$WORK/sdist.txt" || fail "sdist is missing the raw corpus required by ledger tests"
grep -q 'datasets/larkstead/bible/company.yaml' "$WORK/sdist.txt" \
  || fail "sdist is missing the synthetic company bible required by raw-QA tests"
grep -q 'datasets/larkstead/bible/storylines/SL1-alder-mat-defect.yaml' "$WORK/sdist.txt" \
  || fail "sdist is missing the synthetic storylines required by raw-QA tests"
grep -q 'datasets/larkstead/failures/historical-ingest.json' "$WORK/sdist.txt" \
  || fail "sdist is missing immutable corpus history"
grep -q 'datasets/larkstead/pdf/sl2-policy-returns-v2-clean-digital.pdf' "$WORK/sdist.txt" \
  || fail "sdist is missing the deterministic PDF fixture"
grep -q 'datasets/larkstead/pdf/manifest.json' "$WORK/sdist.txt" \
  || fail "sdist is missing the PDF fixture manifest"
grep -q 'datasets/larkstead/qa/generate_pdf_fixtures.py' "$WORK/sdist.txt" \
  || fail "sdist is missing the PDF fixture generator"
printf '  sdist includes raw ledger, synthetic bible and PDF QA inputs\n'

# The wheel is source-only. Assert on what every entry MUST look like rather
# than on shapes a zip cannot contain -- with packages=["src/mastervault"] every
# path is rooted at mastervault/ or the dist-info, so a leaked dataset would
# arrive as `mastervault/datasets/...` and a `^datasets/` pattern would miss it.
if grep -vE '^(mastervault/|mastervault-[^/]*\.dist-info/)' "$WORK/wheel.txt"; then
  fail "wheel contains entries outside the package (listed above)"
fi
if grep -nE '^mastervault/(datasets|tests|docs|scripts)/' "$WORK/wheel.txt"; then
  fail "wheel contains repository data vendored into the package"
fi
printf '  wheel is package-only\n'

# Every ordered migration MUST be packaged: omitting even one leaves clean
# installs or upgrades dependent on the repository checkout.
for MIGRATION in \
  'mastervault/storage/migrations/pg/001_init.sql' \
  'mastervault/storage/migrations/pg/002_migration_ledger.sql' \
  'mastervault/storage/migrations/sqlite/001_init.sql' \
  'mastervault/storage/migrations/sqlite/002_migration_ledger.sql'
do
  grep -q "$MIGRATION" "$WORK/wheel.txt" \
    || fail "wheel is missing an ordered schema migration ($MIGRATION)"
done
printf '  wheel ships every ordered SQLite and PostgreSQL migration\n'

# The prompt files are package data too; without them every contract dies.
grep -q 'mastervault/prompts/grounded_synthesis/v1.md' "$WORK/wheel.txt" \
  || fail "wheel is missing the prompt files"
grep -q 'mastervault/prompts/page_grounded_claim_extraction/v1.md' "$WORK/wheel.txt" \
  || fail "wheel is missing the page-grounded PDF extraction prompt"
grep -q 'mastervault/document_intelligence/docling_artifacts_manifest.json' "$WORK/wheel.txt" \
  || fail "wheel is missing the immutable Docling artifact manifest"
printf '  wheel ships the prompt files and Docling artifact manifest\n'

# No absolute developer paths baked into the metadata.
if unzip -p "$WHEEL" '*/METADATA' | grep -nE '/(Users|home)/[a-z]'; then
  fail "wheel metadata leaks a developer path"
fi
printf '  no developer paths in metadata\n'

# ---------------------------------------------------------------------------
step "clean install"
# ---------------------------------------------------------------------------
VENV="$WORK/venv"
uv venv -q "$VENV"
VIRTUAL_ENV="$VENV" uv pip install -q "$WHEEL"
MV="$VENV/bin/mvault"
[ -x "$MV" ] || fail "the wheel did not install an executable 'mvault'"
printf '  installed into a clean venv\n'

# The layout parser is a genuine opt-in. The core wheel must not drag its
# runtime/model stack into an ordinary installation.
"$VENV/bin/python" - <<'PY' || fail "core install contains optional PDF layout packages"
import importlib.util

for package in ("docling", "docling_core", "docling_ibm_models", "torch", "torchvision"):
    if importlib.util.find_spec(package) is not None:
        raise SystemExit(f"unexpected optional package in core install: {package}")
PY
printf '  core install contains no Docling, torch or torchvision packages\n'

# ---------------------------------------------------------------------------
step "CLI smoke flow from the installed artifact"
# ---------------------------------------------------------------------------
# Run from a scratch directory so nothing resolves back to the repo checkout.
SMOKE="$WORK/smoke"
mkdir -p "$SMOKE"
cd "$SMOKE"
: > "$SMOKE/empty.toml"

export MV_CONFIG="$SMOKE/empty.toml"
export MV_PATHS__WORKSPACE="$SMOKE/ws"
export MV_STORAGE__BACKEND=sqlite
export MV_EMBEDDING__PROVIDER=mock
export MV_LLM__PROVIDER=mock
unset DATABASE_URL

# NB: never pipe a CLI straight into `grep -q` here. Under `set -o pipefail`,
# grep -q exits on its first match, the writer takes SIGPIPE, and the pipeline
# reports failure for a command that actually succeeded. Capture, then assert.
"$MV" --help > "$WORK/help.out" || fail "mvault --help"
"$MV" evidence --help > "$WORK/evidence-help.out" || fail "mvault evidence --help"
"$MV" document --help > "$WORK/document-help.out" || fail "mvault document --help"
"$MV" version > "$WORK/version.out" || fail "mvault version"
# Assert the ACTUAL version, read from pyproject.toml -- `grep 'mastervault '`
# passes on any version, including a stale one, which is exactly the mistake a
# release gate exists to catch.
EXPECTED_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
[ -n "$EXPECTED_VERSION" ] || fail "could not read the version out of pyproject.toml"
grep -qx "mastervault $EXPECTED_VERSION" "$WORK/version.out" \
  || fail "installed wheel reports $(cat "$WORK/version.out"), pyproject says $EXPECTED_VERSION"
printf '  --help, evidence --help and version OK (%s)\n' "$EXPECTED_VERSION"

if "$MV" document doctor --parser docling > "$WORK/docling-doctor.out" 2>&1; then
  fail "core install unexpectedly reported Docling as available"
fi
grep -q "pdf-layout" "$WORK/docling-doctor.out" \
  || fail "missing Docling extra did not produce an actionable install hint"
if grep -qi 'traceback' "$WORK/docling-doctor.out"; then
  fail "missing Docling extra raised a traceback"
fi
printf '  optional Docling selection fails cleanly and actionably\n'

"$MV" init > /dev/null || fail "mvault init"
printf '  init OK\n'

# A minimal vault, then sync -> search -> ask, all through the installed CLI.
VAULT="$SMOKE/ws/vault/operations"
mkdir -p "$VAULT/sources" "$VAULT/wiki"
cat > "$VAULT/wiki/refund-policy.md" <<'EOF'
---
domain: operations
type: wiki
title: Refund Policy
aliases: [refund policy]
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
---

# Refund Policy

## Definition

Refunds are issued within 30 days of delivery for unused items.
EOF
cat > "$VAULT/sources/refund-faq.md" <<'EOF'
---
domain: operations
type: source
source_type: faq
title: Refund FAQ
tags: []
status: processed
created: 2026-01-01
updated: 2026-01-01
key_claims:
  - id: refund-faq-01
    statement: "Refunds are issued within 30 days of delivery for unused items."
    confidence: high
    affects: [refund-policy]
---

# Refund FAQ

Refunds are issued within 30 days of delivery for unused items.
EOF

"$MV" sync > /dev/null || fail "mvault sync"
printf '  sync OK\n'

"$MV" status > "$WORK/status.out" || fail "mvault status"
grep -q sqlite "$WORK/status.out" || fail "mvault status did not report the sqlite backend"
printf '  status OK\n'

"$MV" search "refund policy" > "$WORK/search.out" || fail "mvault search"
grep -q 'refund-faq.md' "$WORK/search.out" || fail "mvault search did not surface the source note"
grep -q '30 days' "$WORK/search.out" || fail "mvault search lost the claim text"
printf '  search OK\n'

"$MV" ask "what is the refund policy?" > "$WORK/ask.out" || fail "mvault ask"
grep -q '30 days' "$WORK/ask.out" || fail "mvault ask lost the grounded fact"
printf '  ask OK\n'

"$MV" lint --mechanical-only > /dev/null || fail "mvault lint on a clean minimal vault"
printf '  lint OK\n'

# The demo dataset ships with the repository, not the wheel. That must fail
# with an actionable message, not a traceback.
if "$MV" demo load > "$WORK/demo.out" 2>&1; then
  fail "mvault demo load unexpectedly succeeded without the repository dataset"
fi
grep -q 'ships with the repository' "$WORK/demo.out" \
  || fail "mvault demo load did not explain where the dataset comes from"
if grep -qi 'traceback' "$WORK/demo.out"; then fail "mvault demo load raised a traceback"; fi
printf '  demo load fails cleanly and actionably\n'

# ---------------------------------------------------------------------------
step "extracted-sdist corpus contract"
# ---------------------------------------------------------------------------
EXTRACTED="$WORK/extracted"
mkdir -p "$EXTRACTED"
tar xzf "$REPO_ROOT/$SDIST" -C "$EXTRACTED" --strip-components=1
(
  cd "$EXTRACTED"
  UV_CACHE_DIR="$WORK/uv-cache" uv sync --extra dev --extra rerank -q
  UV_CACHE_DIR="$WORK/uv-cache" uv run pytest -q \
    tests/integration/test_dataset_integrity.py \
    tests/unit/datasets/test_pdf_fixtures.py
) || fail "tests shipped in the extracted sdist cannot validate their corpus inputs"
printf '  extracted sdist corpus-ledger suite OK\n'

printf '\nPACKAGE CHECK PASSED\n'
