# Template: objection note (Pipewell)

Doc class: SALES-CRM. A structured Pipewell note logged after an objection
surfaces on a call or in email. Read with `../company.yaml` and
`../style-rules.md`.

## Skeleton

```
Opportunity: PW-<slug>
Account: <company name>
Logged: <YYYY-MM-DD> by <initials>
Source: <call YYYY-MM-DD | email YYYY-MM-DD | in person>

Objection (their words): "<verbatim or near-verbatim quote>"
Category: price | competitor | terms | timing | product | support

Response given: <what was actually said, 1-3 sentences>
Evidence used: <specific facts cited: SKUs, prices, policy versions>
Landed: yes | partly | no | unclear

Next: <action> by <YYYY-MM-DD>. Owner: <initials>.
```

## Exemplar (half length)

```
Opportunity: PW-foxglove-winback
Account: Foxglove Studios
Logged: 2026-05-19 by TA
Source: call 2026-05-19

Objection (their words): "ErgoNest is fourteen percent under you and now
they'll lease the seats. Give me a reason to pay more."
Category: competitor

Response given: Total cost over three years, not sticker. Their chairs
have no parts catalog; ours keeps a chair in service for 24.00 casters
or 29.00 armrest pads instead of a replacement seat. 45-day returns
against their 14-day. Support is three people in Portland, not an
outsourced queue.
Evidence used: LS-ACC-008 24.00, LS-ACC-009 29.00, returns policy
effective 2026-01-12, ErgoNest 14-day window.
Landed: partly. Mia conceded the parts point, didn't move on price.
Renewal decision still due 2026-08.

Next: refreshed 26-seat quote plus parts price list by 2026-05-26.
Owner: TA.
```

## Realism notes

- The objection is recorded in the contact's words, kept blunt. Sales reps
  do not launder "give me a reason to pay more" into "the client expressed
  pricing concerns".
- "Landed" is honest. Notes that always say yes are useless to the next
  reader and read fake; "partly" and "no" dominate real pipelines.
- Evidence names checkable facts: SKU prices at the note's date, policy
  versions, competitor terms as reported. Vague evidence ("our quality
  story") is a defect.
- Category comes from the fixed set so the notes can be grouped later.
- Competitor claims stay attributed ("she says fourteen percent"), because
  the rep has not seen the quote. If a storyline says the quote was shared,
  the note can cite its figure directly.
- Ends with a dated next step and owner, like every Pipewell note.
