# Template: CSAT survey export (Helprise)

Doc class: CUSTOMER-SUPPORT. Weekly CSV-style export from Helprise, kept as
plain text records. Read with `../company.yaml` and `../style-rules.md`.

## Skeleton

```
CSAT export: <YYYY-MM-DD> to <YYYY-MM-DD>
Pulled: <YYYY-MM-DD> by <staff full name>
Surveys sent: <N>  Responses: <N>  Response rate: <N of N>

---
Ticket: HD-<YYYY>-<NNNNN>
Agent: <agent full name>
Requester: <customer full name>
Resolved: <YYYY-MM-DD>
Survey sent: <YYYY-MM-DD>
Score: <1-5>
Comment: <verbatim customer text, typos preserved> | (blank)
---
<...more records...>
```

## Exemplar (half length)

```
CSAT export: 2025-02-10 to 2025-02-16
Pulled: 2025-02-17 by Priya Raman
Surveys sent: 9  Responses: 4  Response rate: 4 of 9

---
Ticket: HD-2025-00612
Agent: Jonah Beck
Requester: Ivy Chen
Resolved: 2025-02-13
Survey sent: 2025-02-14
Score: 4
Comment: fast fix and jonah was nice about it. wish the clamp had just
been in the box to start tho
---
Ticket: HD-2025-00598
Agent: Priya Raman
Requester: Marcus Bell
Resolved: 2025-02-11
Survey sent: 2025-02-12
Score: 5
Comment: (blank)
---
```

## Realism notes

- Most surveys go unanswered and most answered ones have no comment. A
  believable export has blank comments on roughly half the responses and a
  response rate near 4 of 9, not 9 of 9.
- Scores skew 4-5 with a thin tail of 1-2. The angry tail usually blames
  the policy or the fee, not the agent by name.
- Comments are verbatim: lowercase, typos, fragments, all preserved. Nobody
  at Larkstead edits customer text, and a polished comment is a tell.
- Every record traces to a real ticket: the score date follows the resolve
  date, the agent matches the ticket assignee, and the requester matches the
  ticket requester.
- Header counts are internally consistent ("Responses: 4" means exactly 4
  records follow with scores).
- corpus-plan CSAT seeds are monthly or quarterly batches (61-132
  responses). Render those as a sampled export: the header cites the seed's
  period and totals ("April 2025: 4.6/5 average on 74 responses; records
  shown: 9 of 74"), then 8-10 records follow. Never print all 74; the
  records-shown line replaces the exact-count rule above for batch exports.
- Low-score records make good storyline hooks (SL2's denied late returns
  produce 1s), but the comment must fit the policy version the agent applied.
