Project: Shopstack checkout tax rounding fix, WA orders
Owner: Ray Lindqvist (RL)
Opened: 2024-10-01   Target close: 2024-10-17
Related: HD-2024-63600

Goal
Fix WA cart tax rounding on multi-item orders and ship a regression set covering all five tax_table rates by 17 Oct.

Plan
- Repro: HD-2024-63600. 79.00 Wren stand (LS-STD-001) plus 18.00 Robin hook (LS-ACC-006), WA order total 97.00. 97.00 x 0.065 = 6.305. Cart shows 6.30.
- Root cause: cart truncates the tax calc at two decimals instead of rounding, only visible when the third decimal lands on 5.
- Fix: cart tax rounding on WA orders, one line, checkout module. Switch the tax multiply to round-half-up at the cent.
- Regression set: 12 synthetic carts built to force a .5 remainder at each tax_table rate, OR 0.0, WA 0.065, CA 0.0725, other_us 0.05, plus a CAN no-tax check. Run against test orders #LS63601 through #LS63603.
- Deploy Thursday, per contract cadence.

Fix touches the shared tax module, same code path for DTC and B2B carts. Scoping this to WA only at the flag level costs one extra line and avoids re-testing the B2B quote path on a Tuesday when Tom's mid-call with a prospect.

Bug's been live since the cart was built, just never surfaced on WA orders often enough to get reported. The 2024-09-01 Wren stand price change to 79.00 is what pushed the volume of two-item WA carts landing on an exact .5 remainder from rare to a weekly occurrence, hence HD-2024-63600 showing up in September and not sooner.

Risks
- Fix regresses the B2B quote path since it shares the tax module: fallback is a feature flag scoped to WA orders only until B2B regression clears, flag pulled once confirmed clean.
- Next contract day slips past 17 Oct: fallback is Dmitri holds current rounding behavior live, no partial deploy midweek.
- Regression set misses a rate combination the audit didn't catch: fallback is Ana flags any cent-level mismatch she finds in the next Ledgerly export, ticket reopens if so, no separate monitoring built for this beyond her normal reconciliation pass.

Log
01 Oct: repro confirmed on HD-2024-63600. 97.00 order, 6.30 shown, 6.31 correct.
03 Oct: root cause found. truncation, not rounding, in the tax multiply.
08 Oct: fix branched. round-half-up at the cent.
10 Oct: regression set built, 12 carts across 5 rates.
15 Oct: 12 of 12 pass. CAN case confirmed no tax collected, as expected.
17 Oct: deployed. HD-2024-63600 closed.
