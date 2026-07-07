# Template: project plan / tracker

Doc class: OPERATIONS. A dated plan with a measurable goal and a running log.
Read with `../company.yaml` and `../style-rules.md`. IDs that belong to a
storyline are allocated in `../storylines/*.yaml`; never mint an ID a
storyline already owns.

## Skeleton

```
Project: <name, include the order or account when there is one>
Owner: <staff full name> (<initials>)
Opened: <YYYY-MM-DD>   Target close: <YYYY-MM-DD>
Related: <order / opportunity / vendor / lot IDs>

Goal
<one measurable sentence: quantity, deadline, success condition>

Plan
- <workstream or wave: dates, counts, owners>
- <...>

Risks
- <risk: trigger and fallback, one line each>

Log
<DD Mon>: <dated entry; entries append over the project's life, newest last>
```

## Exemplar (half length)

```
Project: Cobalt Dental bulk fulfillment, order #LS31695
Owner: Dmitri Okafor (DO)
Opened: 2025-11-21   Target close: 2026-02-18
Related: PW-cobalt-dental-40seat, VEN-04, VEN-02, VEN-07

Goal
Deliver 40 LS-BDL-002 kits plus 2 LS-LMP-001 lamps across four clinics, one
wave per clinic, zero missed wave dates.

Plan
- 40 bundle kits assembled at ParcelPoint (VEN-04) Reno; kitting fee 6.00
  per kit, 240.00 total.
- Components: desk frames from LOT-2025-44 and chair bases from LOT-2025-46,
  both Ostrava Metalworks (VEN-02). 2 Vireo lamps from Meridian (VEN-07)
  PCB rev C stock, LOT-2025-31, ship with wave 1.
- Waves, 10 seats each: clinic 1 on 2025-12-10, clinic 2 on 2026-01-07,
  clinic 3 on 2026-01-28, clinic 4 on 2026-02-18.
- Load: 12 pallets, 122 cartons total; 3 pallets per wave, palletized LTL
  at carrier cost.

Risks
- Kitting slot slippage at ParcelPoint in December: fallback is splitting
  wave 1 across two dock days.

Log
21 Nov: kitting slots booked, 40 kits across four batches.
```

## Realism notes

- Numbers lead. Dmitri's card says number first, context second: 12 pallets,
  122 cartons, never "a lot of freight".
- The Log section grows over the project's life. Later corpus snapshots of
  the same project differ only by appended entries; earlier entries never
  change.
- The goal is one sentence a checker can score: counts and dates, no
  adjectives.
- Each risk names its trigger and its fallback. "Timeline risk" alone is a
  defect.
- Arithmetic must reconcile: 6.00 x 40 kits = 240.00; 12 pallets over 4
  waves = 3 per wave. Checkers recompute these.
- The exemplar condenses SL3's sl3-fulfillment-plan-cobalt (300-500 words
  in the storyline spec).
