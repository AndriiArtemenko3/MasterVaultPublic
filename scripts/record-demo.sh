#!/usr/bin/env bash
# Record an asciinema cast of the MasterVault 5-minute tour, keyless.
#
# Prereqs: asciinema (https://asciinema.org/docs/installation) and the project
# installed (`uv sync`). Produces docs/demo.cast, which you can upload with
# `asciinema upload docs/demo.cast` and embed in the README.
#
# Usage: ./scripts/record-demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v asciinema >/dev/null 2>&1; then
  echo "asciinema not found. Install it first: https://asciinema.org/docs/installation" >&2
  exit 1
fi

OUT=docs/demo.cast
mkdir -p docs

# The tour, driven non-interactively so the cast is reproducible.
read -r -d '' SCRIPT <<'TOUR' || true
set -e
echo '$ mvault init && mvault demo load'
uv run mvault init
uv run mvault demo load
echo
echo '$ mvault search "refund window"'
uv run mvault search "refund window"
echo
echo '$ mvault ask "how many days do customers have to return an item"'
uv run mvault ask "how many days do customers have to return an item"
echo
echo '$ mvault eval'
uv run mvault eval
TOUR

asciinema rec "$OUT" --overwrite --command "bash -lc \"$SCRIPT\""
echo "wrote $OUT — upload with: asciinema upload $OUT"
