# Template: bug report

Doc class: OPERATIONS. Filed against a vendor (firmware, hardware) or an
internal system (Shopstack). Read with `../company.yaml` and
`../style-rules.md`. Bug reports have no dedicated ID grammar; they are keyed
by date plus slug, and by the related HD tickets. IDs that belong to a
storyline are allocated in `../storylines/*.yaml`.

## Skeleton

```
Bug report: <slug-style title>
Date: <YYYY-MM-DD>
Filed by: <staff full name> (<initials>)
Filed with: <vendor VEN-NN, or internal assignee>
System under report: <firmware version / tool / component>
Related tickets: <HD-YYYY-NNNNN, ... or count plus first ticket>

Summary
<one sentence: what breaks, on what hardware or config, since when>

Reproduction steps
1. <setup>
2. <trigger>
3. <observed failure, with a measurable symptom>

Affected population
<lots, unit counts, exposure figures, each with its base>

Evidence
<ticket count to date; logs; what does NOT reproduce>

Ask
<the specific fix, analysis, or decision requested, with a date if one exists>
```

## Exemplar (half length)

```
Bug report: vireo-v12-sleep-dim-flicker
Date: 2025-12-19
Filed by: Dmitri Okafor (DO)
Filed with: Bitgrove Labs (VEN-03)
System under report: Vireo firmware v1.2, released 2025-12-09
Related tickets: 7 to date, first HD-2025-05402 (2025-12-11)

Summary
LS-LMP-001 units on Meridian PCB rev C flicker at roughly 0.5 Hz in
sleep-mode dim on firmware v1.2.

Reproduction steps
1. LS-LMP-001 with Meridian PCB rev C, firmware v1.2.
2. Enable the sleep-mode dim schedule.
3. Set brightness below 20 percent.
4. Flicker at roughly 0.5 Hz appears within 30 seconds.

Affected population
Meridian Components (VEN-07) PCB rev C: LOT-2025-31, 400 units, received
22 Sep; LOT-2025-38, 350 units, received 17 Nov. 750 rev C units total.

Evidence
7 tickets to date. Rev B units on v1.2 do not reproduce the flicker.

Ask
Root-cause analysis plus a rollback option to v1.1 for rev C units.
```

## Realism notes

- Reproduction steps end in a measurable symptom: 0.5 Hz, below 20 percent,
  within 30 seconds. "It flickers sometimes" is a defect.
- Always state what does not reproduce. That line is what narrows the cause,
  and later documents (the v1.3 release notes, the postmortem) build on it.
- Every population figure carries its base: 750 rev C units, 7 tickets,
  400 + 350 across two lots. Checkers recompute the sums.
- Vendor-filed reports name the vendor by VEN code. Internal Shopstack bugs
  go to Ray Lindqvist, who replies Tuesdays and Thursdays and writes
  commit-message status notes ("fix cart tax rounding on WA orders").
- A follow-up is a new dated document referencing the first (SL4 files
  bitgrove-bug-report-flicker-02 on 2026-01-06), never an edit in place.
- Ops register, zero typos in the report body; the related customer tickets
  carry the typos, not this document.
