# Template: integration guide

Doc class: OPERATIONS. How two tools from the fictional stack connect: data
flow, mapping, failure modes. Read with `../company.yaml` and
`../style-rules.md`. Only tools from company.yaml's `tools` and `vendors`
lists may appear; the banned_strings list is matched as a substring.

## Skeleton

```
Integration guide: <source system> to <target system>
Systems: <tool> (<category>) -> <tool> (<category>)
Owner: <staff full name> (<initials>)   Maintainer: <staff full name> (<initials>)
Written: <YYYY-MM-DD>
Updated: <YYYY-MM-DD>: <one line on what changed>   <- append, never rewrite

Schedule
<when it runs, timezone named>

Access
<who holds which login; least privilege stated plainly>

Field mapping
| <source> field | <target> destination | note |
|---|---|---|

Failure modes
- <what breaks>: <how to detect it and the recovery path>

Change control
<who may change the mapping, on what cadence, tested where>

Verification
<the recurring check that proves the integration is healthy, with owner>
```

## Exemplar (half length)

```
Integration guide: Shopstack order journal to Ledgerly
Systems: Shopstack (storefront) -> Ledgerly (accounting)
Owner: Ana Petrova (AP)   Maintainer: Ray Lindqvist (RL)
Written: 2024-05-14

Schedule
Nightly export at 02:15 Pacific. The feed covers orders captured up to
midnight; refunds post on the refund date, not the order date.

Access
Ana holds Shopstack admin and the Ledgerly import queue. Ray holds
Shopstack admin only.

Field mapping
| Shopstack field | Ledgerly destination | note |
|---|---|---|
| order subtotal | 4000 Sales | |
| discount total | 4090 Discounts | contra-revenue |
| shipping collected | 4200 Shipping income | zone rates per shipping policy |
| sales tax collected | 2300 Sales tax payable | sub-account per ship-to state: 2300-WA, 2300-CA, 2300-US |
| refunds | 4100 Returns and allowances | posts on refund date |

Failure modes
- Order edited after its nightly export does not re-sync. Detect on the
  Friday reconciliation; fix via Shopstack, Journal feed, Re-send, then
  confirm the corrected line in Ledgerly.
- Feed outage: Shopstack retries at 03:15 and 04:15, then mails
  ana@larkstead.example.

Change control
Mapping changes go through Ray (Tuesdays and Thursdays per contract) with
Ana's approval, tested against the Ledgerly sandbox before the nightly run.

Verification
Ana reconciles the week's journal lines against Shopstack order totals
every Friday.
```

## Realism notes

- Fictional tools only. Shopstack, Helprise, Pipewell, Ledgerly,
  ParcelPoint, Mailloft; a real product name anywhere fails the corpus.
- The mapping table is concrete: named accounts, named sub-accounts. "Maps
  to the right account" is a defect.
- Every failure mode has a detection signal and a recovery path, and the
  recovery is a sequence a reader could click through.
- Guides age honestly. This one is written 2024-05-14 and cannot mention
  policies or SKUs born later; refreshes append an Updated line rather
  than silently rewriting.
- Ray's contract cadence (Tuesdays and Thursdays) constrains any change
  dates a corpus document claims.
- Zero typos; this shares the policy register.
