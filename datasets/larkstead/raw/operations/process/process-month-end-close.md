Process: Month-end close
Effective: 2024-03-01
Owner: Ana Petrova   Teams: finance, operations
Trigger: last calendar day of the month

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | invoice cutoff | AP | Ledgerly | every vendor invoice and Shopstack sales batch dated within the month posted by 5pm the first business day after month-end |
| 2 | accrual list | AP | Ledgerly | open purchase orders with goods received but not yet billed, plus recurring charges not yet invoiced, listed with dollar amounts |
| 3 | reconciliation | AP | Ledgerly | bank statement cash balance matches the ledger cash balance to the cent |
| 4 | lock | AP | Ledgerly | prior month locked; no postings without a dated adjusting entry, initialed AP |
| 5 | sign-off | AP/MV | email | one-page summary sent to Mara; any variance over 50.00 called out by line |

Rules
- Self-approve under 250.00, manager band 250.00 to 999.99, CEO sign-off from 1000.00, effective 2024-01-15. Any accrual adjustment that crosses a band needs the matching signature before the lock goes in.
- ParcelPoint kitting runs 6.00 per kit. Kits assembled but not yet on a ParcelPoint invoice go on the accrual list at that rate, not estimated.
- Zero-variance rule: a bank-to-ledger gap over 0.01 gets traced the same day it turns up. It never carries into the next month as a plug number, and Ana has said so more than once in the finance thread.

Example run (March 2024 close)
2024-04-01, cutoff run: three late vendor invoices posted, INV-OSM-2024-036 from Ostrava Metalworks and two from GreenCrate Packaging, plus the full Shopstack batch through 2024-03-31. If a late invoice lands after the 5pm cutoff, it holds for April instead of being forced into March.
2024-04-02, accrual list drafted: 358 bundle kits assembled at ParcelPoint in March, not yet on an invoice, accrued at 2,148.00.
2024-04-03, reconciliation: ledger ran 0.15 short against the bank statement. Traced to a Canadian order where tax was correctly left uncollected but the parcel surcharge posted a cent light on rounding. Corrected same day, no plug.
2024-04-04: month locked, no further postings without a dated adjusting entry.
2024-04-05: summary emailed to Mara. Nothing over 50.00 to flag this month.

Ledgerly closed on March, opening balances carried to April clean.
