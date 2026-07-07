Process: Shopstack contractor work-request queue
Effective: 2025-07-01
Owner: Dmitri Okafor   Teams: ops, sales, support, engineering (contract)
Trigger: any staff member finds a Shopstack, Pipewell, or Ledgerly-integration defect or change request that needs code

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | log | requester | email | request written up with repro steps; HD ticket number attached if one exists |
| 2 | triage | DO | email | priority set same day, P1 or P2 |
| 3 | schedule | DO | email | slotted for the next Tuesday or Thursday; Ray confirms scope the day before |
| 4 | build | RL | Shopstack, Pipewell | fix built and tested against the acceptance criteria in the request |
| 5 | verify | requester | same system the bug was found in | requester reruns the original reproduction steps |
| 6 | close | DO | queue log | marked closed once verification passes, or carried to the next window |

Rules
- Ray's contract caps him at Tuesday and Thursday only. Anything logged Friday through Monday waits for the next Tuesday, no exceptions for urgency alone.
- P1 needs a live order or an open B2B negotiation on the line, and still waits for Ray's next contracted day. No same-day fix outside those two days.
- Acceptance criteria go into the request before scheduling: a named sample size or reproduction case, not "make sure it works."
- Every request cites the HD ticket number if a customer ticket already exists. Internal-only finds get logged with just a discovery date.
- This formalizes what Ray and Priya were already doing ad hoc, like the WA tax-rounding fix in June: logged, Ray took it his next Tuesday, fixed and verified same day, HD-2025-61315.

Example run
Fri 29 Aug 2025: Yuki logs a finding, no HD ticket, nothing customer-facing yet. The Shopstack B2B quote PDF export drops every row past line 12 with no on-screen error. She confirms it with a 14-row test quote spanning two seat blocks; rows 13 and 14 vanish from the export though the on-screen total stays correct. Priority: P2, logged for the record ahead of a larger proposal going out soon.

Mon 01 Sep 2025: Dmitri confirms scope with Ray by end of day. Acceptance criteria: the same 14-row test quote must export complete across as many pages as needed, page breaks instead of a hard cutoff.

Tue 02 Sep 2025: Ray raises the template's row limit and adds a page-break rule. Yuki reruns the 14-row test quote same afternoon; both pages print, totals check out. Request closed same day.

Next steps: 1) rerun the test on any quote already in draft with more than 12 lines, 2) flag Dmitri if the fix does not hold on a live 20-plus seat quote before month end.
