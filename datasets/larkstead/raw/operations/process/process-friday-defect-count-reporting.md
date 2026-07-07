Process: Friday defect-count reporting
Effective: 2025-08-01
Owner: Dmitri Okafor   Teams: support, warehouse, ops
Trigger: every Friday morning, a pull of Helprise tickets closed in the trailing 7 days carrying a product-defect tag

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | tag check | PR/JB/CM | Helprise | every ticket closed that week naming a physical defect carries the SKU's existing product tag |
| 2 | pull | DO | Helprise | Friday export of defect-tagged tickets, counted per SKU for the week and the trailing 30 days |
| 3 | cross-check | DO | Ledgerly | Friday count checked against Hank's Monday returns-grading log so a defect isn't counted twice, once as a ticket and once as a Grade C return |
| 4 | flag | DO | report note | any SKU crossing a trend threshold marked amber or red |
| 5 | notify | DO | email | red-flagged SKUs go to the relevant owner same day |
| 6 | file | DO | shared log | report filed in the running defect log, week closed |

Rules
- Trend thresholds: 5 or more defect-tagged tickets on one SKU in the trailing 30 days flips it amber, logged but no outbound note yet. 10 or more in the same window flips it red and triggers a same-day note to the SKU's vendor owner.
- A SKU already under an active lot quarantine or open vendor claim is excluded from the trend count for that period, so a problem already being worked doesn't also sit on this report as if it were new.
- This replaces the informal pattern-spotting inside the Monday queue triage close-out with a systematic per-SKU count. Monday's triage stays about ticket age and load, not product trend, and the two reports are not meant to duplicate each other.
- Tagging here rides on whatever product tag already exists in Helprise; it does not require the full retro-tagging taxonomy that comes later.

Example run (week of 25-29 Aug 2025, pulled Fri 29 Aug 2025)
Two tickets close this week tagged against LS-ACC-005, the Willow grommet cover: HD-2025-64301 and HD-2025-64302, both reporting the pair not seating flush on desktops over 1.25 inches thick. Trailing 30-day count for that SKU comes to 6, which crosses the amber threshold. No prior lot quarantine or vendor claim is open on it, so the count stands as is.

LS-ARM-001-SGL and LS-ARM-001-DBL together close one ticket this week, HD-2025-64303, resolved through the standard arm-drift fix rather than a replacement. Trailing 30-day count is 3, well under the amber line, stays green.

Dmitri cross-checks against Hank's Monday log: no Grade C returns on either SKU this week, so nothing to reconcile. The grommet cover gets an amber flag on the report, filed as-is, no vendor note yet since it hasn't reached red. Report closed 29 Aug 2025. Next pull: 05 Sep 2025.
