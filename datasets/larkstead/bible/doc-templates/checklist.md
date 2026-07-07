# Template: checklist

Doc class: OPERATIONS. A numbered list of checkable items plus one filled run.
Read with `../company.yaml` and `../style-rules.md`. IDs that belong to a
storyline are allocated in `../storylines/*.yaml`; never mint an ID a
storyline already owns.

## Skeleton

```
Checklist: <title>
Version effective: <YYYY-MM-DD>
Owner: <staff full name> (<initials>)
Run for: <order / lot / account / event this run covers>
Run date: <YYYY-MM-DD>   Completed by: <initials>

1. [ ] <one checkable fact or action, expected value inline>
2. [ ] <item>
...
N. [ ] <item>

Notes
<deviations; unchecked items with reasons; n/a items with reasons>

Sign-off: <initials>, <YYYY-MM-DD>
```

## Exemplar (half length)

```
Checklist: Cobalt Dental account onboarding
Version effective: 2025-12-01
Owner: Yuki Tanaka (YT)
Run for: order #LS31695 (PW-cobalt-dental-40seat), delivery wave 1 of 4
Run date: 2025-12-10   Completed by: YT

1. [x] Delivery wave confirmed with Sam Ortiz: wave 1 on 2025-12-10;
       remaining waves 2026-01-07, 2026-01-28, 2026-02-18.
2. [x] Warranty registration filed for all 42 units under the 1 year
       standard warranty, effective 2024-01-15.
3. [x] Support routing configured: Helprise B2B queue, account tag
       cobalt-dental.
4. [x] Single point of contact recorded: Sam Ortiz, office manager.
5. [x] Desk assembly guide sent to the clinic 1 site contact.
6. [ ] LS-ACC-011 leveling feet sets offered for uneven floors. Clinic 1
       declined; re-offer at each wave.
7. [x] Post-wave check-in call scheduled for 2025-12-16, within 5 business
       days of delivery.

Notes
Item 6 open by design; items 5 through 7 repeat for each wave.

Sign-off: YT, 2025-12-10
```

## Realism notes

- Every item is checkable: a value, a date, or an artifact. "Confirm
  everything looks good" is a defect.
- A filled run shows real state. An unchecked box carries a reason; a run
  with every box ticked and an empty Notes block reads fake.
- The owner's voice card shapes item phrasing. Yuki numbers everything,
  writes formally, and uses absolute dates only.
- Version effective date belongs to the checklist itself; the run block is
  dated separately and repeats per run.
- Quantities must reconcile with allocated canon: 42 units here is 40
  LS-BDL-002 kits plus 2 LS-LMP-001 lamps on order #LS31695, per SL3.
- Checklists end on the sign-off line. No summary paragraph after it.
