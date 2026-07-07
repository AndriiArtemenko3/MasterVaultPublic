---
domain: operations
type: source
title: Vireo V12 Flicker Postmortem
tags:
- postmortem
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: postmortem
key_claims:
- id: postmortem-vireo-v12-flicker-postmortem-01
  statement: Firmware v1.2 shipped over the air on 2025-12-09 and introduced a visible 0.5 Hz flicker on Vireo smart desk lamps.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-02
  statement: Twelve customers filed tickets between 2025-12-11 and 2026-01-14 regarding the flicker issue.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-03
  statement: Support agents shipped a workaround within two weeks for the flicker issue.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-04
  statement: A staged rollback to v1.1 contained most of the flicker exposure in January 2026.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-05
  statement: Hotfix firmware v1.3 closed the defect on 2026-02-17.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-06
  statement: One B2B account, Cobalt Dental Group, was affected on two units and is now fully resolved.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-07
  statement: The ticket rate for the flicker issue was 1.9 percent against 618 exposed units.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-08
  statement: 592 of 618 units were restored by the staged rollback that occurred between January 9-13, 2026.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-09
  statement: The goodwill warranty extension covered all 618 Vireo V12 affected units, including those at Cobalt Dental Group.
  confidence: high
  affects: []
- id: postmortem-vireo-v12-flicker-postmortem-10
  statement: The v1.2 sleep-timer wake interrupt collides with the Meridian PCB rev C LED driver's PWM cycle at brightness below 20 percent.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/postmortem/vireo-v12-flicker-postmortem.md
provenance_hash: e453562e3c96d3ca
---

# Vireo V12 Flicker Postmortem

## Summary

Postmortem: Vireo firmware v1.2 sleep-dim flicker Published: 2026-02-24 Author: Dmitri Okafor Incident window: 2025-12-09 to 2026-02-17 Status: closed This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

## Content

Postmortem: Vireo firmware v1.2 sleep-dim flicker
Published: 2026-02-24
Author: Dmitri Okafor
Incident window: 2025-12-09 to 2026-02-17
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

Summary
Firmware v1.2 shipped over the air on 2025-12-09 and introduced a visible 0.5 Hz flicker on Vireo smart desk lamps (LS-LMP-001) built with Meridian PCB rev C, whenever sleep-mode dimming brought brightness below 20 percent. Twelve customers filed tickets between 2025-12-11 and 2026-01-14. [[support]] agents shipped a workaround within two weeks, a staged rollback to v1.1 contained most of the exposure in January, and hotfix firmware v1.3 closed the defect on 2026-02-17. One B2B account, Cobalt Dental Group, was affected on two units and is now fully resolved.

Impact
- 12 tickets against 618 exposed units, a 1.9 percent ticket rate.
- 592 of 618 units restored by the staged rollback (09-13 Jan); the remaining 26 recovered afterward through direct support contact or the v1.3 push.
- Cost: 4,800.00 under SOW Amendment No. 2 (INV-BGL-2026-006), plus the ongoing 2-year goodwill warranty exposure on all 618 vireo-v12-affected units.

Timeline
| date | event |
|---|---|
| 2025-12-09 | firmware v1.2 released via the Bitgrove Labs (VEN-03) OTA service |
| 2025-12-11 | first flicker ticket, HD-2025-05402, filed by Jonah Beck |
| 2025-12-18 | pattern escalated internally: six identical tickets in seven days |
| 2025-12-19 | vendor bug report filed with Bitgrove Labs; same day, Cobalt Dental Group reports flicker on both of its add-on units |
| 2025-12-22 | customer troubleshooting guide published by Priya Raman |
| 2026-01-06 | follow-up bug report with debug log captures narrows the root cause |
| 2026-01-09 | staged rollback to v1.1 begins; 592 of 618 units restored by 13 Jan |
| 2026-01-20 | Mara Voss issues a 2-year goodwill warranty, scope vireo-v12-affected |
| 2026-01-26 | Ana Petrova executes SOW Amendment No. 2 with Bitgrove for the hotfix |
| 2026-02-17 | hotfix v1.3 released; v1.1 channel pin removed |

Root cause
The v1.2 sleep-timer wake interrupt collides with the Meridian PCB rev C LED driver's PWM cycle whenever duty drops below 20 percent, producing the roughly 0.5 Hz flicker. Rev B boards use a different LED driver and are unaffected under identical firmware and dim-schedule conditions.

Contributing factors
- Bitgrove's test bench had no rev C hardware at the time v1.2 was built, so the release shipped untested against the board revision that ultimately triggered the defect.
- The affected population spans two receiving lots, LOT-2025-31 and LOT-2025-38, both shipped from October 2025 onward, which meant the earliest exposed customers were also our newest ones.

What went well
- The disable-sleep-dim workaround was confirmed on the fourth ticket (HD-2025-05461, 2025-12-15) and published as a customer-facing guide within a week, on 2025-12-22.
- The staged rollback restored 592 of 618 units within five days once Bitgrove delivered the signed v1.1 image.
- The goodwill warranty extension closed the coverage gap for every affected unit, including the two on the Cobalt Dental Group account, before the hotfix even shipped.

What went poorly
- The first six tickets were worked as individual complaints for roughly a week before the pattern was escalated internally on 2025-12-18, which delayed the first vendor bug report by several days.
- 26 units could not take the January rollback because they were offline, and needed either individual outreach or the v1.3 push in February to recover.
- The 2024 public help doc for the Vireo lamp was never updated to reflect the goodwill extension, so it still states the 1-year standard warranty with no mention of the vireo-v12-affected scope.

Follow-ups
| action | owner | due | status |
|---|---|---|---|
| add rev C units to the Bitgrove test bench | VEN-03 | 2026-02-10 | done |
| qualify Meridian (VEN-07) rev D | DO | 2026-03-31 | in progress |
| track warranty scope vireo-v12-affected in Helprise | PR | 2026-01-26 | done |
| update the public Vireo help doc for the warranty scope | PR | -- | not started |
