Bug report: shopstack-cart-wa-tax-rounding
Date: 2024-09-19
Filed by: Priya Raman (PR)
Filed with: Ray Lindqvist (contract Shopstack developer)
System under report: Shopstack cart checkout, tax calculation module
Related tickets: 5 to date -- HD-2024-63701, HD-2024-63704, HD-2024-63709, HD-2024-63713, HD-2024-63718 (first HD-2024-63701, 2024-09-05)

Summary
On Washington orders, the cart rounds tax down instead of up whenever the subtotal times 6.5 percent lands on a value ending in .x05, which has undercharged five Washington customers by a cent apiece since the 09-01 Wren price change pushed more carts onto that boundary.

Reproduction steps
1. Add a Wren laptop stand (LS-STD-001, 79.00 at the post-09-01 price) and a Robin headphone hook (LS-ACC-006, 18.00) to a cart shipping to a Washington address.
2. Proceed to checkout. Subtotal reads 97.00.
3. Cart applies the 6.5 percent WA rate: 97.00 x 0.065 = 6.305. The tax field shows 6.30. It should show 6.31.

Affected population
Five live Washington orders since 05 Sep landed exactly on this boundary, most recently order #LS63722 on 16 Sep, subtotal 97.00, tax posted 6.30 instead of 6.31. No other zone has reported it, though nothing rules out the same fault sitting under an untested combination elsewhere in the catalog.

Evidence
All five tickets are Washington addresses, all five under-collected by exactly one cent, no exceptions. Oregon carts never trip it, since Oregon carries no sales tax. Ray reproduced the fault on 18 Sep with the same stand-and-hook combination and traced it to the checkout module truncating the third decimal instead of rounding half up.

Ask
Ship the rounding fix inside the October checkout tax project, already scoped. Until then, Ana should true up the five known one-cent shortfalls at October close so the WA tax remittance isn't short.
