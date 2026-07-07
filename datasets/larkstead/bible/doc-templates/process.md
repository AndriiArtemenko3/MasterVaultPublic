# Template: process document

Doc class: OPERATIONS. A cross-team flow with stages, owners, and exit
criteria. Use `sop.md` instead when one role executes one task. Read with
`../company.yaml` and `../style-rules.md`. IDs that belong to a storyline are
allocated in `../storylines/*.yaml`.

## Skeleton

```
Process: <name>
Effective: <YYYY-MM-DD>
Owner: <staff full name>   Teams: <sales, finance, ops, ...>
Trigger: <the event that starts the process>

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|

Rules
- <policy citations with effective dates; approval thresholds; exceptions>

Example run
<a real storyline instance walked through the stages, dated>
```

## Exemplar (half length)

```
Process: B2B quote-to-cash
Effective: 2026-03-16
Owner: Ana Petrova   Teams: sales, finance, operations
Trigger: inbound B2B lead (Shopstack B2B form, showroom, or referral)

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | opportunity | TA/YT | Pipewell | PW-<slug> open; seat count and budget on the note |
| 2 | quote | TA | Pipewell | proposal sent at the tier in force; quotes hold 30 days |
| 3 | approval | AP | email | any discount exception at or over 1000.00 signed by MV |
| 4 | order | TA | Shopstack | #LS order at prices in force on the order date |
| 5 | invoice | AP | Ledgerly | Net-30 issued; totals per the invoice template arithmetic |
| 6 | fulfillment | DO | ParcelPoint | delivery plan published; waves scheduled |
| 7 | close | TA/YT | Pipewell | closed-won; account owner assigned |

Rules
- Volume tier per the sheet in force: 15% at 25+ units (2024-06-01 through
  2025-12-07); 12% at 30+ units from 2025-12-08. Signed deals keep
  contracted pricing.
- SL3 exception: one-time extra 3% on 30+ seats (effective 2025-10-06);
  CEO sign-off from 1000.00 in exception value, [APPROVAL] subject line.
- Restocking fee waived on B2B returns of 10+ units (effective 2025-06-02).

Example run (PW-cobalt-dental-40seat)
- 2025-06-24 inbound via the Shopstack B2B form; opportunity opened.
- 2025-08-26 and 2025-09-09 discovery calls; budget ceiling 38000.00.
- 2025-09-18 proposal v1 at the 15% tier, net 40868.00; declined 2025-10-01.
- 2025-11-05 [APPROVAL] memo for the 3% SL3 exception, value 1318.80;
  MV signed 2025-11-10.
- 2025-11-11 proposal v2 on 40x LS-BDL-002 plus 2 lamps, total 36345.20;
  closed-won 2025-11-14 as order #LS31695.
- 2025-11-21 fulfillment plan; four waves, 2025-12-10 through 2026-02-18.
```

## Realism notes

- Exit criteria must be observable: a document exists, a status flips, a
  signature lands. "Stage complete when ready" is a defect.
- Rules cite the policy version by effective date, and the process document
  itself carries a date; a 2025-06 process doc quotes the 15% tier and is
  correct for its date.
- The example run comes from a storyline, never from invention; every date
  above traces to SL3.
- Process documents describe what actually happens, including known leaks
  (the SL3 one-pager that was never pulled from the shared drive).
- Stage owners are initials from company.yaml staff; systems come from the
  tools list.
- Zero typos; policy register.
