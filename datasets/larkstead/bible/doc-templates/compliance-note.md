# Template: compliance note

Doc class: INTERNAL-ADMIN. A dated snapshot of an obligation and how the
company meets it. Read with `../company.yaml` (tax_table, vendors,
policies) and `../style-rules.md`. Never name real regulators, statutes,
or software; the fictional tax_table is the only tax law this corpus has.

## Skeleton

```
Compliance note: <topic>
Date: <YYYY-MM-DD>
Author: <staff full name>
Status: <informational | action needed by YYYY-MM-DD>
Next review: <YYYY-MM-DD>

Position
<what the company does today, with rates, dates, and systems named>

Obligations
| obligation | detail | where handled |
|---|---|---|

Open items
- <gap or conditional, with owner and date>
```

## Exemplar (half length)

```
Compliance note: Sales tax collection by destination
Date: 2026-03-02
Author: Ana Petrova
Status: informational
Next review: 2027-03-01

Position
Shopstack applies tax by ship-to state: Oregon 0%, Washington 6.5%,
California 7.25%, other US states 5%. Canadian orders ship
tax-uncollected; the customer settles import charges at delivery.
Collected tax posts to the Ledgerly 2300 sub-accounts (2300-WA, 2300-CA,
2300-US) through the nightly journal feed.

Obligations
| obligation | detail | where handled |
|---|---|---|
| WA collection | 6.5% on WA-destination orders, B2B quotes included | Shopstack checkout; sales quotes |
| CA collection | 7.25% on CA-destination orders | Shopstack checkout |
| remittance | quarterly, from the 2300 sub-account balances | Ledgerly, AP |

Open items
- If PW-tidewater-14seat closes, then the quote must carry WA tax; the
  office is in Vancouver, WA. Owner TA, check at proposal stage.
```

## Realism notes

- Rates come from tax_table and nowhere else. Importing real state rates
  or naming a real tax authority breaks the fiction.
- A compliance note is a snapshot: a later note supersedes it as a new
  dated document; nothing is edited in place.
- Open items read like Ana: conditional phrasing ("If X closes, then"),
  an owner, and a checkpoint. A gap without an owner is a defect.
- Tie obligations to canon entities: Tidewater's Vancouver WA address is
  in company.yaml and is the reason the WA line exists.
- Insurance, lease, and legal obligations (Nehalem Mutual renews each
  July; Lovejoy rent escalates 3% each June) make good sibling notes; keep
  one topic per note.
- Zero typos; policy register; concrete numbers over vague assurance.
