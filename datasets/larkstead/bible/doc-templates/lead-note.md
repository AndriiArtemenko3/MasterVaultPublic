# Template: CRM lead / opportunity note (Pipewell)

Doc class: SALES-CRM. System of record: Pipewell.
Read with `../company.yaml` and `../style-rules.md`. Opportunity slugs follow
`PW-<slug>` and are allocated in company.yaml or a storyline; reuse, never remint.

## Skeleton

```
Opportunity: PW-<slug>
Account: <company name>
Contact: <name (role)>
Stage: discovery | proposal | negotiation | closed-won | closed-lost | churn-risk
Seats: <N>
Est. value: <USD, list basis, or "tbd">
Note by: <initials>, <YYYY-MM-DD>

<body: fragments allowed. what happened, what they said, numbers heard,
competitor mentions, blockers. no narrative polish.>

Next: <one concrete action> by <YYYY-MM-DD>. Owner: <initials>.
```

## Exemplar (half length)

```
Opportunity: PW-kestrel-10seat
Account: Kestrel Financial Planning
Contact: Grace Liu (operations coordinator)
Stage: discovery
Seats: 10
Est. value: 11,490.00 list (10x LS-BDL-002 at 1,149.00)
Note by: TA, 2026-05-12

First came in through the showroom back in December; returned 2026-05-09
and came back with two colleagues today. 10 seats, moving offices in
August. Grace is holding an ErgoNest per-seat
lease offer and asked straight out why buying beats leasing. Told her:
parts catalog, 45-day returns, support here in Portland. She wants that
in writing. 10 seats is under our 30-seat tier so this is a list-price
deal, no discount room. Budget owner is a partner she didn't name.

Next: 10-seat quote plus lease-vs-buy one-pager by 2026-05-15. Owner: TA.
```

## Realism notes

- Every note names the opportunity as `PW-<slug>` and ends with a dated
  next step and an owner. A note without a next step is a defect per
  style-rules.
- Fragments are fine; polish is not. CRM notes are written in the two
  minutes after a call, and the register should show it.
- Author voice holds: Tom leads with the seat count and dollar figure and
  names contacts by first name after first contact. Yuki's notes are full
  sentences with absolute dates.
- Estimated value shows its arithmetic (qty x unit price) so checkers can
  verify it against price_history for the note's date.
- Competitor intel arrives as reported speech ("Grace is holding an
  ErgoNest lease offer"), not as verified fact. Unknowns stay named as
  unknowns ("a partner she didn't name").
- Stage must be consistent with company.yaml's stage for the account as of
  the note's date, and with any storyline beats in the same window.
