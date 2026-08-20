# Generation-aware query configuration

MasterVault's ordinary `search`, `claims`, `wiki`, and `ask` commands share one
versioned read-selection contract. The default remains compatible with v0.2:
when neither `<workspace>/change_control/state.sqlite3` nor any
`query_generation` locator is configured, `--generation auto` opens the
ordinary backend selected by `storage.backend` and `DATABASE_URL`.

Supplying any `query_generation` value is an assertion that managed authority
already exists. A missing authority database, incomplete runtime locators, or
evidence that does not reproduce its durable identities fails closed. Managed
resolution never bootstraps authority, creates repository roots, migrates
state, rebuilds an index, heals a receipt, or writes operator navigation.

## Selectors

| Selector | Resolution |
|---|---|
| `auto` | The unmanaged v0.2 backend when no managed state/configuration exists; with initialized authority and its complete runtime locators, the exact active generation. Partial managed state/configuration fails closed. |
| `active` | Require initialized authority plus its complete runtime locators and serve the exact active generation. |
| `legacy` | Require initialized generic-workspace authority and serve its attested generation-zero legacy index, even when generation one is active. |
| `mgeneration:<sha256>` | Serve that exact lower-case, 64-hex generation ID when it is generation zero or the first managed successor in the bounded authority chain. |

The current v0.3 read resolver supports generation zero and one managed
successor only. A sealed-seed authority does not expose a generic generation-zero
legacy index, so `legacy` is unsupported for that profile.

## Result identity

Human output starts with `knowledge generation:`. JSON output from `search` and
`ask` carries `generation`, a path-free schema-v1 object with:

- the original versioned selection;
- the resolved backend and generation kind (`unmanaged-v0.2`,
  `generation-zero`, or `managed-generation`);
- served generation ID/number, current active generation ID, active authority
  revision, and `is_active`;
- generation manifest SHA-256, logical index fingerprint, exact physical index
  SHA-256/byte count, storage schema version, and embedding model/dimensions.

Unmanaged v0.2 metadata leaves managed identity fields empty and reports
`is_active = false`. Absolute workspace, source-root, seed, evidence,
canonical, and managed-generation repository paths are runtime locators only;
they are not durable authority and never appear in generation metadata.

The configured embedding provider must reproduce the selected index's model
and dimensions. Managed reads use immutable/query-only SQLite connections and
revalidate both index evidence and active authority before output. If active
generation one is missing, corrupt, substituted, stale, or mismatched, `auto`
and `active` return an integrity error instead of falling back to generation
zero.

Secure SQLite authority readers and writers coordinate on the already-pinned
private authority directory, separate from SQLite's database inode. A query
holds a shared lock for its complete resolved-generation lifetime; a writer
holds an exclusive lock from transaction start through commit verification.
An overlap therefore resolves to one exact pre-activation or post-activation
snapshot, while a bounded lock timeout is reported as a conflict rather than
as evidence corruption.

## Generic workspace authority

For a workspace bootstrapped through the generic ADR 0016 path, configure its
manifest and every external logical root named by that manifest:

```toml
[storage]
backend = "sqlite"

[paths]
workspace = "/absolute/path/to/vault_workspace"

[query_generation]
# Relative paths resolve below `paths.workspace`. The manifest must stay
# outside `vault/` and `change_control/`.
bootstrap_manifest = "bootstrap/workspace-bootstrap-v1.json"

# Omit this map when the manifest uses only the workspace root. Keys must
# exactly match its logical source-root IDs; values are runtime-only paths.
source_roots = { "governing-sources" = "/absolute/path/to/source-root" }
```

That is sufficient while generation zero is active. Read support for an
already-valid internally produced first managed successor also requires the
exact sealed seed, immutable inference evidence, and canonical governing-source
repositories used by its durable decision. The generic incoming/admission
workflow that produces such a successor is intentionally deferred; this
configuration does not create or activate one. For that read-only profile,
replace the preceding `[query_generation]` block with:

```toml
[query_generation]
bootstrap_manifest = "bootstrap/workspace-bootstrap-v1.json"
source_roots = { "governing-sources" = "/absolute/path/to/source-root" }
seed_repository_root = "/absolute/path/to/sealed-seed-repository"
evidence_repository_root = "/absolute/path/to/inference-evidence-repository"
canonical_repository_root = "/absolute/path/to/canonical-source-repository"

# Optional in this profile. When present, it must match the SHA derived from
# the active managed decision exactly.
temporal_analysis_manifest_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

The resolver reopens all configured content against SQLite authority on every
managed query. These paths therefore locate evidence; they do not replace its
content-addressed IDs, receipts, or hashes.

## Sealed-seed authority

The sealed-seed profile is retained for the existing deterministic managed
authority. With no generic workspace manifest, all four locators are required:

```toml
[storage]
backend = "sqlite"

[paths]
workspace = "/absolute/path/to/vault_workspace"

[query_generation]
seed_repository_root = "/absolute/path/to/sealed-seed-repository"
evidence_repository_root = "/absolute/path/to/inference-evidence-repository"
canonical_repository_root = "/absolute/path/to/canonical-source-repository"
temporal_analysis_manifest_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

This profile serves an already-active generation one only. Use a generic
workspace bootstrap manifest when generation-zero reads must be available.

## SQLite and run-persistence boundaries

Managed selection is SQLite-only. Set `storage.backend = "sqlite"` and leave
`DATABASE_URL` unset; an explicit PostgreSQL backend, or `auto` with
`DATABASE_URL` present, is rejected before managed repositories or providers
are opened. This restriction does not change unmanaged v0.2 PostgreSQL query
behavior.

Managed authority resolution also requires POSIX `flock`, descriptor-relative
no-follow opens, `O_DIRECTORY`, and `O_NOFOLLOW`; hosts without those primitives
(including Windows) report the managed profile as unsupported.

Managed `ask` is query-only from MasterVault's persistence perspective. It
uses an in-memory budget ledger and an ephemeral `ask-query-only-*` run ID, and
does not create a run directory, event log, per-round artifact, or summary.
Unmanaged v0.2 `ask` continues to use `RunContext` and persists its normal run
record. Provider calls and provider-owned caches are separate from this
MasterVault persistence guarantee.
