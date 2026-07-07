# Template: B2B proposal / quote

Doc class: SALES-CRM. PDF sent from Pipewell; plain-text source kept here.
Read with `../company.yaml` and `../style-rules.md`. Zero typos; exact cents.

## Skeleton

```
Proposal for: <account name>
Opportunity: PW-<slug>
Prepared by: <staff full name>, Larkstead Goods Co.
Date: <YYYY-MM-DD>
Valid through: <YYYY-MM-DD, typically date + 30 days>

## Scope

<2-4 sentences: what is being outfitted, seat count, delivery constraints
the customer named.>

## Pricing

| SKU | Description | Qty | Unit list | Extended |
|---|---|---|---|---|
| <SKU> | <name> | <N> | <0.00> | <0.00> |

Discount: <"none (below 30-unit tier)" | "12% volume tier (30+ units,
tier sheet effective 2025-12-08): -0.00">
Subtotal: <0.00>
Sales tax (<STATE> <rate>%): <0.00>
Freight: <LTL, quoted at delivery scheduling | zone rate line>
Total: <0.00> <plus freight if unquoted>

## Terms

<payment terms; delivery plan; warranty citing the version in force;
returns citing the version in force>

<staff full name>
<email@larkstead.example>
```

## Exemplar (half length)

```
Proposal for: Tidewater Insurance Brokers
Opportunity: PW-tidewater-14seat
Prepared by: Tom Aldridge, Larkstead Goods Co.
Date: 2026-05-06
Valid through: 2026-06-05

## Scope

14 workstations for the Vancouver, WA office, single delivery. Each seat
is a Canopy team bundle: 60 in desk, Rowan chair, single monitor arm,
cable kit.

## Pricing

| SKU | Description | Qty | Unit list | Extended |
|---|---|---|---|---|
| LS-BDL-002 | Canopy team bundle (per seat) | 14 | 1,149.00 | 16,086.00 |

Discount: none (below 30-unit tier)
Subtotal: 16,086.00
Sales tax (WA 6.5%): 1,045.59
Freight: LTL to Vancouver, WA, quoted at delivery scheduling
Total: 17,131.59 plus freight

## Terms

Payment net 30 from delivery. Delivery within 3 weeks of signed order.
1-year warranty on all items (warranty policy effective 2024-01-15).
Returns per the 45-day window; the 10% restocking fee is waived on B2B
orders of 10 or more units (restocking revision effective 2025-06-02).

Tom Aldridge
tom@larkstead.example
```

## Realism notes

- Arithmetic is checker-verified: qty x unit list = extended, discounts
  computed on the subtotal, tax from tax_table on the taxable base, all to
  the cent. 16,086.00 x 0.065 = 1,045.59.
- Unit prices are the price_history entries in force on the proposal date,
  and the discount line names the tier sheet version it applied. Deals signed
  before 2025-12-08 keep the old 15%-at-25 tier; new paper never does.
- Tax follows the ship-to state (WA here, per company.yaml's note on the
  Tidewater account), not Larkstead's Oregon address.
- Freight on heavy items is never free. Either a real LTL line or "quoted
  at delivery scheduling"; a proposal that silently ships desks free is a
  defect.
- Terms cite policy versions by effective date so a reader can date the
  document from its contents alone.
- No adjectives in the pricing table and no persuasion in the terms. The
  selling happened on the call; the proposal is arithmetic.
