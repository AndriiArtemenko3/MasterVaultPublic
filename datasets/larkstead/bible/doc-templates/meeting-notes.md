# Template: meeting notes

Doc class: INTERNAL-ADMIN. Bullet fragments with an owner and a date on
every action item. Read with `../company.yaml` and `../style-rules.md`.
Disagreements get recorded, not resolved by the note-taker. Notes end on
the action list.

## Skeleton

```
Meeting: <name>
Date: <YYYY-MM-DD>   Attendees: <initials, comma-separated>   Notes: <initials>

<topic 1>
- <fragment: who said what; numbers kept exact>
- <decision, attributed>
- <disagreement recorded as stated, unresolved>

<topic 2>
- <...>

Actions
| action | owner | due |
|---|---|---|
```

## Exemplar (half length)

```
Meeting: margin review
Date: 2025-12-01   Attendees: MV, AP, TA, DO, SG   Notes: AP

b2b discount tiers
- AP: average realized B2B discount ran above plan for 2025.
- decision (MV): tier moves to 12% at 30+ units, effective 2025-12-08.
  Signed deals keep contracted pricing.
- TA: wants open proposals grandfathered at 15% through 2026-01-31; says a
  mid-pipeline change risks the 30-seat deals. MV: no. Disagreement noted.

2026 price list
- DO: Ostrava (VEN-02) steel surcharge on the 2025-11 invoices; frames and
  chair bases cost more from November.
- new list effective 2026-01-15: desks +30.00, chairs +20.00, dual arm
  +10.00, lamp +10.00, bundles +50.00. Free shipping threshold moves from
  75.00 to 99.00 the same day.
- SG: what did the 2025-03 mat move to 59.00 do to attach rate? AP to pull
  the number before the announce email.

Actions
| action | owner | due |
|---|---|---|
| publish tier sheet v2 | AP | 2025-12-08 |
| pull the 2024-06 pricing one-pager from the shared drive | TA | 2025-12-12 |
| mat attach-rate number since 2025-03-01 | AP | 2025-12-09 |
| price-change customer email, Mailloft campaign Squall | SG | 2026-01-05 |
| Shopstack price update with RL (Tuesday slot 2026-01-13), live 2026-01-15 | DO | 2026-01-13 |
```

## Realism notes

- Fragments, not prose. Attribution by initials; the note-taker's own voice
  still shows (Ana keeps cents on every figure).
- Every action row has an owner and an absolute date. An action without
  both is a defect.
- Disagreements survive on the page exactly as voiced. Tom's grandfathering
  objection stays unresolved here; company.yaml records him quoting the old
  15% sheet on a 2026-01 Nordlicht call, which is the payoff.
- Recorded actions are commitments, not history: the one-pager pull (due
  2025-12-12) never happens, which seeds contradiction C3 in SL3. Never
  write a later doc that quietly completes an action a storyline says
  was dropped.
- Decisions cite effective dates that must match company.yaml's policy and
  price_history entries to the day.
- Notes end on the action table. No closing summary paragraph.
