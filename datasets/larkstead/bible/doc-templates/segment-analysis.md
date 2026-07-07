# Template: segment analysis (internal)

Doc class: SALES-CRM. Internal memo analyzing a customer or account segment,
usually from a Pipewell or Shopstack export. Read with `../company.yaml` and
`../style-rules.md`.

## Skeleton

```
Analysis: <title stating the question>
Date: <YYYY-MM-DD>
Author: <staff full name>
Data: <source system export, window, pull date, n>

## Question

<one sentence.>

## Table

| <segment> | <count or figure> | <figure> |
|---|---|---|

## Findings

1. <claim with numbers stating their base: "2 of 12", never a bare 17%>
2. <...>

## Caveats

<sample size, missing data, what this cannot support.>

Next: <action> by <YYYY-MM-DD>. Owner: <initials>.
```

## Exemplar (half length)

```
Analysis: B2B accounts by seat band, 2026-Q1
Date: 2026-04-02
Author: Tom Aldridge
Data: Pipewell export pulled 2026-04-01; all 12 B2B accounts on the
books as of 2026-03-31.

## Question

Where do deals stall: small, mid, or large seat counts?

## Table

| Seat band | Accounts | Closed-won | Churn-risk |
|---|---|---|---|
| 8-15 seats | 6 | 4 | 1 |
| 18-30 seats | 4 | 1 | 1 |
| 40+ seats | 2 | 1 | 0 |

## Findings

1. Small accounts close: 4 of 6 in the 8-15 band are won, and all four
   pay list because they sit under the 30-seat tier.
2. The mid band stalls. 3 of 4 accounts between 18 and 30 seats are in
   negotiation, proposal, or churn-risk; this is exactly the band where
   ErgoNest quotes show up (Foxglove, Nordlicht).
3. Both churn-risk accounts have different causes: Stonebridge traces to
   the LOT-2025-14 mat defect, Foxglove to a quote 14% under ours. One
   fix is operational, one is commercial. Don't average them.

## Caveats

n=12. No trend claims; one closed deal moves any percentage by 8 points.
Petrel's 15 drop-ship addresses inflate small-band order counts.

Next: mid-band playbook draft (18-30 seats) by 2026-04-18. Owner: TA.
```

## Realism notes

- Percentages state their base or stay as counts. "4 of 6" survives a
  fact-check; "67%" invites one. With n=12 the memo says so out loud.
- The data line makes the memo reproducible: system, pull date, window,
  n. A checker can rebuild the table from the account list in company.yaml.
- Findings are falsifiable claims, numbered, each carrying its numbers.
  No finding restates the table in prose without adding a comparison.
- Caveats are real limits, not humility theater. Naming the Petrel
  drop-ship distortion is what an operator would actually flag.
- Author voice holds even in analysis: Tom leads with seat counts and
  writes "Don't average them." Sofia would open by asking for the metric.
- The memo answers one question. Sprawl into strategy belongs in a
  different doc; the Next line hands off instead.
