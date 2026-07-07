---
domain: operations
type: source
title: Sop Vireo Ota Release Hardware Matrix
tags:
- checklist
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: checklist
key_claims:
- id: sop-sop-vireo-ota-release-hardware-matrix-01
  statement: The document is effective on 2026-03-12.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-02
  statement: Dmitri Okafor is the owner of the document.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-03
  statement: The document applies to Operations and support.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-04
  statement: 'Two hardware revisions are live: rev C and rev D.'
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-05
  statement: Meridian (VEN-07) rev D is qualified on 09 Mar 2026.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-06
  statement: Every Vireo release runs the full matrix against both hardware revisions before any staged push.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-07
  statement: At least 3 bench units on rev C and 3 on rev D are required.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-08
  statement: Build numbers must match what Bitgrove filed to proceed with testing.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-09
  statement: A clean bench pass allows staging of the release to 5 percent of the live fleet.
  confidence: high
  affects: []
- id: sop-sop-vireo-ota-release-hardware-matrix-10
  statement: If there are fewer than 2 tagged tickets in the 5 percent stage, it expands to 25 percent.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-vireo-ota-release-hardware-matrix.md
provenance_hash: 3798e17c8863d52b
---

# Sop Vireo Ota Release Hardware Matrix

## Summary

SOP: Vireo OTA release test and staged rollout checklist Effective: 2026-03-12 Owner: Dmitri Okafor (DO) Applies to: Operations, [[support]] Systems: Helprise, Bitgrove OTA console, Mailloft Purpose 2 hardware revisions are live in the field now that Meridian (VEN-07) rev D is qualified, and every Vireo release runs the full matrix against both before any staged push goes out. No release skips a cell, no exceptions for a hotfix that looks small.

## Content

SOP: Vireo OTA release test and staged rollout checklist
Effective: 2026-03-12
Owner: Dmitri Okafor (DO)
Applies to: Operations, support
Systems: Helprise, Bitgrove OTA console, Mailloft

Purpose
2 hardware revisions are live in the field now that Meridian (VEN-07) rev D
is qualified, and every Vireo release runs the full matrix against both
before any staged push goes out. No release skips a cell, no exceptions for
a hotfix that looks small.

Prerequisites
- A signed build number from Bitgrove Labs (VEN-03) for the release under
  test, with release notes filed against that number
- At least 3 bench units on rev C and 3 on rev D, both currently one build
  behind the release under test
- Staging-channel permission on the Bitgrove OTA console

Steps
1. Confirm the build number and release notes match what Bitgrove filed.
   Mismatched notes stop the checklist here, go back to Bitgrove before
   touching a bench unit.
2. Flash the build to the rev C bench units first. Run the 20-minute
   sleep-and-wake cycle 3 times per unit, watching brightness ramp below
   20 percent duty the whole time on each pass.
3. Flash the same build to the rev D bench units. Same 3 cycles, same watch
   point, same log format.
4. Any flicker, stutter, or failed reconnect across the 6 bench units
   combined: stop, do not stage, log it, and go to Escalation.
5. Clean bench pass on both revisions: stage the release to 5 percent of the
   live fleet, split evenly across rev C and rev D where the fleet mix
   allows it.
6. Hold the 5 percent stage 48 hours. Watch Helprise for any new ticket
   tagged with the release's build number.
7. Fewer than 2 tagged tickets in that window: expand to 25 percent, then to
   100 percent 3 days later if the second window stays just as quiet.
8. 2 or more tagged tickets against the 5 percent stage, or a single ticket
   describing flicker on either revision, rolls the stage back to the last
   known-good build. No further expansion without a rev C and rev D root
   cause writeup from Bitgrove.

Escalation
Any bench failure goes to Dmitri and Bitgrove the same day, not queued for
the next standup. A rollback triggered from the field stage goes to Mara
within the hour, given the rev C history she wants every rollback pattern
watched, not just logged.

Close-out
Record the build number, bench result, stage percentages and hold dates, and
the final rollout date in the release log. A rolled-back release gets a
one-line reason, no more.

References
- Bitgrove Labs (VEN-03), Net-30
- Meridian Components (VEN-07), rev D qualified 09 Mar 2026
- Warranty policy: vireo-v12-affected scope, effective 2026-01-20
