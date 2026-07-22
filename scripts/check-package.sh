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
FORBIDDEN='(^|/)(\.env|\.env\..*|\.coverage|\.coverage\..*|coverage\.xml|htmlcov|\.pytest_cache|\.ruff_cache|\.mypy_cache|__pycache__|vault_workspace|_build|node_modules|\.venv)(/|$)|\.(key|pem|pyc|pyo|so|log)$|junit-.*\.xml$|BUILD_LOG\.md|MORNING_REPORT\.md'
# `.env.example` is a documented, secret-free template and is meant to ship;
# every other .env variant is not.
ALLOWED='(^|/)\.env\.example$'
if grep -Ei "$FORBIDDEN" "$WORK/all.txt" | grep -Ev "$ALLOWED" | grep -E '.'; then
  fail "distribution contains a workspace, cache, secret, or test artifact (listed above)"
fi
printf '  no workspaces, caches, secrets or test output\n'

# Corpus-generation inputs are repository history, not distribution content.
if grep -nE 'datasets/larkstead/(raw|bible)/' "$WORK/all.txt"; then
  fail "distribution contains corpus-generation inputs (raw/ or bible/)"
fi
printf '  no corpus-generation inputs (raw/, bible/)\n'

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

# The Postgres schema MUST be packaged: shipping without it broke `mvault init`
# against Postgres from an installed wheel in 0.1.x.
grep -q 'mastervault/storage/migrations/pg/001_init.sql' "$WORK/wheel.txt" \
  || fail "wheel is missing the Postgres schema (storage/migrations/pg/001_init.sql)"
printf '  wheel ships the Postgres schema\n'

# The prompt files are package data too; without them every contract dies.
grep -q 'mastervault/prompts/grounded_synthesis/v1.md' "$WORK/wheel.txt" \
  || fail "wheel is missing the prompt files"
printf '  wheel ships the prompt files\n'

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
"$MV" version > "$WORK/version.out" || fail "mvault version"
# Assert the ACTUAL version, read from pyproject.toml -- `grep 'mastervault '`
# passes on any version, including a stale one, which is exactly the mistake a
# release gate exists to catch.
EXPECTED_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
[ -n "$EXPECTED_VERSION" ] || fail "could not read the version out of pyproject.toml"
grep -qx "mastervault $EXPECTED_VERSION" "$WORK/version.out" \
  || fail "installed wheel reports $(cat "$WORK/version.out"), pyproject says $EXPECTED_VERSION"
printf '  --help and version OK (%s)\n' "$EXPECTED_VERSION"

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

printf '\nPACKAGE CHECK PASSED\n'
