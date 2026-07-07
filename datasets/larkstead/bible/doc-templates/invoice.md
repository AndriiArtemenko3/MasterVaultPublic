# Template: invoice

Doc class: INTERNAL-ADMIN. Zero typos; every number re-derivable. Read with
`../company.yaml` (skus.price_history, vendors.payment_terms, tax_table,
policies.shipping_zones) and `../style-rules.md`.

Two directions share one arithmetic layout:
- Inbound vendor bill: carries an `INV-<VENDORCODE>-<YYYY>-<NNN>` number;
  the INV grammar is reserved for these.
- Outbound customer invoice: keyed by the `#LS<NNNNN>` order number; it
  never mints an INV ID.

## Arithmetic rules (checkers verify every line)

```
line total   = qty x unit price, exact to the cent
subtotal     = sum of line totals
total        = subtotal - discount + shipping + tax

discount     names its rate and its base (e.g. "15% of 43960.00")
tax base     = subtotal - discount; shipping is never taxed
tax rate     by ship-to state per tax_table: OR 0.0, WA 0.065, CA 0.0725,
             other US 0.05; CAN ships tax-uncollected
tax rounding once, at order level, half up to the cent
unit price   = price_history entry in force on the order date
terms        inbound from vendors.payment_terms; due = invoice date + days
shipping     heavy items always bill the zone rate; the free-shipping
             threshold (75.00; 99.00 from 2026-01-15) waives light and
             medium rates only

Worked micro-example (outbound, delivered 2025-09, Vancouver WA):
1x LS-CHR-001-GRY at 389.00 = 389.00; discount 0.00; shipping 49.00
(US-1 heavy, always billed); tax = 389.00 x 0.065 = 25.285 -> 25.29;
total 389.00 + 49.00 + 25.29 = 463.29.
```

## Skeleton

```
Invoice: <INV-XXX-YYYY-NNN (inbound) | for order #LSNNNNN (outbound)>
Invoice date: <YYYY-MM-DD>
Terms: <Net-30 | Net-45>   Due: <invoice date + term days>
From: <issuer legal name>
To: <payer legal name, ship-to state named when tax applies>

| qty | item | description | unit price | line total |
|---|---|---|---|---|
| <n> | <SKU or service> | <plain description> | <0.00> | <qty x unit> |

Subtotal                                    <sum>
Discount (<name, rate, base>)              -<amount>
Shipping (<zone + weight class | freight note>)  +<amount>
Tax (<ST> <rate> on <subtotal - discount>)      +<amount>
Total due                                   <subtotal - discount + shipping + tax>

<payment and terms note>
```

## Exemplar A: outbound B2B invoice (half length)

```
Invoice for order #LS31695
Invoice date: 2025-11-17
Terms: Net-30   Due: 2025-12-17
From: Larkstead Goods Company, LLC, Portland OR
To: Cobalt Dental Group, Portland OR (four Oregon delivery sites)

| qty | item | description | unit price | line total |
|---|---|---|---|---|
| 40 | LS-BDL-002 | Canopy team bundle | 1099.00 | 43960.00 |
| 2 | LS-LMP-001 | Vireo smart desk lamp | 149.00 | 298.00 |

Subtotal                                          44258.00
Discount (line 1 only: 15% volume tier 6594.00
  + 3% SL3 exception 1318.80, both on 43960.00)   -7912.80
Shipping (palletized LTL at carrier cost per
  clinic, billed separately per proposal v2)          0.00
Tax (OR 0.0% on 36345.20)                             0.00
Total due                                         36345.20

Payment by ACH to the account on file. Reference #LS31695.
```

## Exemplar B: inbound vendor bill (half length)

```
Invoice: INV-VRD-2025-118
Invoice date: 2025-06-24
Terms: Net-45   Due: 2025-08-08
From: Verdant Textiles Co. (VEN-01), Vietnam
To: Larkstead Goods Company, LLC; goods received 2025-06-19 at
ParcelPoint Reno as LOT-2025-14

| qty | item | description | unit price | line total |
|---|---|---|---|---|
| 600 | LS-MAT-001-CHL | Alder desk mat, charcoal | 12.90 | 7740.00 |
| 500 | LS-MAT-001-SND | Alder desk mat, sand | 12.90 | 6450.00 |
| 300 | LS-MAT-001-SGE | Alder desk mat, sage | 12.90 | 3870.00 |

Subtotal                                          18060.00
Discount                                              0.00
Shipping (inbound freight billed separately by
  Cascadia Freight, VEN-06)                           0.00
Tax (imported goods, none collected)                  0.00
Total due                                         18060.00

Net-45 per the Verdant supply agreement. Remit by ACH to the account on
the agreement.
```

## Realism notes

- Every figure must survive recomputation: 44258.00 - 7912.80 = 36345.20,
  which equals the signed Cobalt total in SL3; 1400 x 12.90 = 18060.00 per
  the SL1 spine. Never round a subtotal to make a total work.
- Discounts name rate and base. The Cobalt discount applies to line 1
  only; lamps sold at list stay outside it, and the tax base reflects that.
- The tax line appears even at zero, with the state and rate shown ("OR
  0.0%"), so checkers can verify the rate lookup, not just the sum.
- Terms come from vendors.payment_terms for inbound bills (Verdant is
  Net-45; date + 45 days = the due date, to the day). Outbound B2B terms
  are Net-30 house practice.
- A credit memo is a separate dated document referencing the original INV
  ID; the original invoice is never edited and never annotated with later
  events. SL1's credit memo (1638.30, applied 2025-09-02 against
  INV-VRD-2025-118) is its own document; the June invoice above cannot know
  about it. A dated document contains nothing from after its own date.
- Vendor unit prices (12.90) are invoice prices, not the landed unit_cost
  in company.yaml (14.50 = 12.90 + 1.60 freight and receiving, per SL1).
