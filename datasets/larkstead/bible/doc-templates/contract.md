# Template: contract / amendment

Doc class: INTERNAL-ADMIN. Numbered sections, quoted defined terms, zero
typos, absolute dates. Read with `../company.yaml` and `../style-rules.md`.
Colefield & Tran LLP (VEN-10) drafted the B2B MSA template (2024-06) and
vendor addenda; contracts reference their governing master agreement by date.

## Skeleton

```
<TITLE IN CAPS>

This <agreement type> ("<Short Name>") is made <YYYY-MM-DD> (the
"Effective Date") between Larkstead Goods Company, LLC ("<Role>") and
<counterparty legal name> ("<Role>"<, VEN-NN for vendors>).

<Optional recital: governing master agreement by date; internal approval
reference when the expense policy requires one.>

1. Definitions
1.1 "<Term>" means <definition, referencing SKUs, IDs, and dates exactly>.
1.2 "<Term>" means <...>.

2. <Substantive section>
2.1 <obligation with a number, a date, or both>
2.2 <...>

3. <Fees / payment>
3.1 <amount to the cent; invoice ID when allocated; Net terms per
    vendors.payment_terms>

N. Signatures
Larkstead Goods Company, LLC          <Counterparty>
<name, title, date>                   <name, title, date>
```

## Exemplar (half length)

```
AMENDMENT NO. 2 TO STATEMENT OF WORK

This amendment ("Amendment") is made 2026-01-26 (the "Amendment Date")
between Larkstead Goods Company, LLC ("Client") and Bitgrove Labs
("Vendor", VEN-03), and amends the statement of work under the master
services agreement dated 2024-03-01 (the "Master Agreement"). Client's
internal spend approval was recorded 2026-01-23 under the expense policy
effective 2025-07-01.

1. Definitions
1.1 "Hotfix" means firmware release v1.3 for the Vireo smart desk lamp
    (LS-LMP-001), correcting the sleep-dim defect on Meridian PCB rev C
    units.
1.2 "Rollback Image" means the signed firmware v1.1 image pinned to the
    OTA channel for rev C serials, delivered before the Amendment Date.
1.3 "Monitoring Period" means the 90 days following the Hotfix release.

2. Scope
2.1 Vendor delivers the Hotfix no later than 2026-02-17.
2.2 The Rollback Image is accepted as delivered.
2.3 Vendor provides defect monitoring and triage through the Monitoring
    Period.

3. Fee and invoicing
3.1 Client pays a fixed fee of 4,800.00 for the work in Section 2.
3.2 Vendor invoices the fee as INV-BGL-2026-006 on Net-30 terms per the
    Master Agreement.

4. Order of precedence
4.1 This Amendment controls over the Master Agreement where they conflict;
    all other terms of the Master Agreement stand.

5. Signatures
Larkstead Goods Company, LLC          Bitgrove Labs
Mara Voss, CEO, 2026-01-26            Renata Kohl, Principal, 2026-01-26
```

## Realism notes

- Defined terms are quoted and capitalized at first definition, then used
  capitalized and never redefined. A lowercase "hotfix" after 1.1 is a
  defect.
- Every obligation carries a number, a date, or both. "Promptly" and
  "reasonable efforts" appear only where a real small-business contract
  would stay loose, never on money or delivery dates.
- Money, invoice IDs, and Net terms must reconcile with company.yaml and
  the storyline spine (4,800.00, INV-BGL-2026-006, Net-30 are SL4 canon).
- Counterparty signatories may be derived names, but once used they are
  canon corpus-wide (Lan Pham signs for Verdant in SL1; Renata Kohl signs
  for Bitgrove from this exemplar on).
- Warranty and policy citations use the version in force on the Effective
  Date, not the newest one.
- Zero typos, absolute dates only, no em-dash asides.
