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
  'mastervault/storage/migrations/pg/003_structural_records.sql' \
  'mastervault/storage/migrations/sqlite/001_init.sql' \
  'mastervault/storage/migrations/sqlite/002_migration_ledger.sql' \
  'mastervault/storage/migrations/sqlite/003_structural_records.sql' \
  'mastervault/change_control/migrations/sqlite/001_change_control_aggregate.sql' \
  'mastervault/change_control/migrations/sqlite/002_authoritative_human_review.sql'
do
  grep -q "$MIGRATION" "$WORK/wheel.txt" \
    || fail "wheel is missing an ordered schema migration ($MIGRATION)"
done
printf '  wheel ships every ordered index and change-control migration\n'

grep -q 'mastervault/change_control/workflow.py' "$WORK/wheel.txt" \
  || fail "wheel is missing the temporal-review workflow seam"
grep -q 'docs/decisions/0007-langgraph-durable-temporal-review-wait.md' "$WORK/sdist.txt" \
  || fail "sdist is missing the LangGraph temporal-review ADR"
unzip -p "$WHEEL" '*/METADATA' > "$WORK/metadata.txt"
grep -q '^Requires-Dist: langgraph==1.2.9$' "$WORK/metadata.txt" \
  || fail "wheel metadata is missing the pinned direct LangGraph dependency"
grep -q '^Requires-Dist: langgraph-checkpoint-sqlite==3.1.0$' "$WORK/metadata.txt" \
  || fail "wheel metadata is missing the pinned direct SQLite checkpoint dependency"
if grep -qE '^Requires-Dist: langchain(-core)?([;<=> ]|$)' "$WORK/metadata.txt"; then
  fail "wheel metadata declares a forbidden direct langchain dependency"
fi
unzip -p "$WHEEL" 'mastervault/change_control/workflow.py' > "$WORK/workflow.py"
if grep -qE '(^|[[:space:]])(from|import)[[:space:]]+langchain(_core)?([[:space:].]|$)' \
  "$WORK/workflow.py"; then
  fail "temporal-review workflow imports langchain or langchain_core directly"
fi
printf '  workflow seam, ADR and pinned direct dependencies are shipped\n'

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

# Exercise the authority/checkpoint split from the installed wheel only. This
# is a library smoke: no temporal-review CLI is claimed in this milestone.
(
  cd "$WORK"
  PYTHONPATH= "$VENV/bin/python" - <<'PY'
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from mastervault.change_control import (
    ChangeControlAggregate,
    ClaimRevisionRegistry,
    ClaimSourceReference,
    ComparableClaimPair,
    DependencyRegistry,
    DocumentAuthority,
    DocumentReplacementAssessment,
    DocumentReplacementSet,
    DocumentRole,
    DocumentVersionMetadata,
    DocumentVersionRegistry,
    HumanReviewDecisionCommand,
    HumanReviewRequestCommand,
    OrchestrationPhase,
    PairDisposition,
    RelationAssessment,
    RelationGraph,
    ReviewDecisionItem,
    ReviewDisposition,
    ReviewSubjectKind,
    ReviewSubjectRef,
    SqliteChangeControlStore,
    TemporalConstraintSet,
    TemporalConstraintStatus,
    TemporalReviewWorkflow,
    VersionedClaimRevision,
)


def document(document_id: str, version: str, effective: date, sha: str):
    return DocumentVersionMetadata.create(
        document_id=document_id,
        document_family="operations.installed-wheel-policy",
        version_label=version,
        source_path=f"sources/{document_id}.md",
        source_sha256=sha,
        declared_effective_from=effective,
        role=DocumentRole.POLICY,
        authority=DocumentAuthority.PRIMARY,
    )


def claim(document_version, local_id: str, statement: str, sha: str):
    return VersionedClaimRevision.create(
        document=document_version,
        source=ClaimSourceReference(
            source_note_path=f"operations/sources/{document_version.document_id}.md",
            source_note_sha256=sha,
            source_claim_id=local_id,
        ),
        statement=statement,
        declared_effective_from=document_version.declared_effective_from,
        scopes=("installed-wheel-policy",),
    )


with TemporaryDirectory() as temporary:
    workspace = Path(temporary)
    state_path = workspace / "change_control" / "state.sqlite3"
    checkpoint_path = workspace / "change_control" / "checkpoints.sqlite3"
    old_document = document("policy-v1", "v1", date(2025, 1, 1), "a" * 64)
    new_document = document("policy-v2", "v2", date(2026, 1, 1), "b" * 64)
    old_claim = claim(old_document, "policy-v1-01", "The policy allows 30 days.", "c" * 64)
    new_claim = claim(new_document, "policy-v2-01", "The policy allows 45 days.", "d" * 64)
    relation = RelationAssessment.create(
        pair=ComparableClaimPair.create(old_claim, new_claim),
        disposition=PairDisposition.SUPERSEDES,
        rationale="The later policy replaces the earlier policy.",
        confidence=0.99,
        newer_revision_id=new_claim.claim_revision_id,
    )
    replacement = DocumentReplacementAssessment.create(
        newer_document=new_document,
        older_document=old_document,
        status=TemporalConstraintStatus.PROPOSED,
        rationale="The whole policy replacement awaits human review.",
        confidence=0.99,
    )
    aggregate = ChangeControlAggregate.create(
        aggregate_id="installed-wheel",
        documents=DocumentVersionRegistry.create((old_document, new_document)),
        claims=ClaimRevisionRegistry.create((old_claim, new_claim)),
        relation_graph=RelationGraph.create((relation,)),
        dependencies=DependencyRegistry.create(()),
        document_replacements=DocumentReplacementSet.create((replacement,)),
        temporal_constraints=TemporalConstraintSet.create(()),
    )
    store = SqliteChangeControlStore(state_path)
    store.init_schema()
    seeded = store.create(aggregate, operation_id="installed-wheel-seed")
    requested = store.create_review_request(
        HumanReviewRequestCommand(
            aggregate_id=aggregate.aggregate_id,
            expected_revision=seeded.revision,
            expected_aggregate_sha256=seeded.aggregate_sha256,
            subjects=(
                ReviewSubjectRef(
                    kind=ReviewSubjectKind.DOCUMENT_REPLACEMENT,
                    subject_id=replacement.relation_id,
                ),
            ),
            requester_id="packaging@example.com",
            rationale="Exercise the installed durable review wait seam.",
        ),
        operation_id="installed-wheel-request",
    )
    store.close()
    with TemporalReviewWorkflow(
        requested.request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        assert workflow.start().phase == OrchestrationPhase.WAITING
    store = SqliteChangeControlStore(state_path)
    snapshot = requested.request.subjects[0]
    decided = store.decide_review(
        HumanReviewDecisionCommand(
            request_id=requested.request.request_id,
            reviewer_id="packaging-reviewer@example.com",
            rationale="Reject the packaging-only proposed replacement.",
            items=(
                ReviewDecisionItem(
                    kind=snapshot.kind,
                    subject_id=snapshot.subject_id,
                    original_subject_sha256=snapshot.subject_sha256,
                    disposition=ReviewDisposition.REJECTED,
                ),
            ),
        ),
        operation_id="installed-wheel-decision",
    )
    store.close()
    with TemporalReviewWorkflow(
        requested.request,
        authority_path=state_path,
        checkpoint_path=checkpoint_path,
    ) as workflow:
        assert workflow.status().phase == OrchestrationPhase.RECONCILIATION_PENDING
        status = workflow.resume()
        assert status.phase == OrchestrationPhase.COMPLETE
        assert status.decision == decided.decision
PY
) || fail "installed wheel temporal-review workflow smoke"
printf '  installed wheel temporal-review wait/reconcile smoke OK\n'

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
