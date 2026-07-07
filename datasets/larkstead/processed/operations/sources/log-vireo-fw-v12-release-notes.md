---
domain: operations
type: source
title: Vireo Fw V12 Release Notes
tags:
- other
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: other
key_claims:
- id: log-vireo-fw-v12-release-notes-01
  statement: Vireo firmware v1.2 is released on 2025-12-09.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-02
  statement: The publisher of the firmware is Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-03
  statement: The update applies to all LS-LMP-001 units.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-04
  statement: The ambient-sensor overshoot issue in bright rooms is fixed.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-05
  statement: The ambient sensor recalibration runs once per boot instead of once per week.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-06
  statement: There are no known issues open at release.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-07
  statement: The staged OTA rollout is 20 percent on 09 Dec, 50 percent on 11 Dec, 100 percent on 13 Dec.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-08
  statement: The update applies the same way across every Vireo unit in the field.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-09
  statement: No customer-facing action is needed for the update.
  confidence: high
  affects: []
- id: log-vireo-fw-v12-release-notes-10
  statement: Tag any related tickets fw-v1.2 for tracking.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/log/vireo-fw-v12-release-notes.md
provenance_hash: 138a412dbbf4ae61
---

# Vireo Fw V12 Release Notes

## Summary

Release notes: Vireo firmware v1.2 Released: 2025-12-09 Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor Applies to: all LS-LMP-001 units Fixed - Occasional ambient-sensor overshoot in bright rooms that caused the lamp to dim briefly when a door opened nearby. Recalibrated the sensor read cycle.

## Content

Release notes: Vireo firmware v1.2
Released: 2025-12-09
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units

Fixed
- Occasional ambient-sensor overshoot in bright rooms that caused the lamp to dim briefly when a door opened nearby. Recalibrated the sensor read cycle.

Changed
- Sleep-mode dimming schedule reworked: transitions between brightness steps are now smoother and the lamp holds each step slightly longer before easing to the next.
- Ambient sensor recalibration runs once per boot instead of once per week.

Known issues
- None open at release.

Rollout
Staged OTA to all connected LS-LMP-001 units: 20 percent on 09 Dec, 50 percent on 11 Dec, 100 percent on 13 Dec. Manual path for anyone who wants it sooner: Vireo app, Settings, Check for update.

Support
No hardware revisions are called out in this release package; the update applies the same way across every Vireo unit in the field. No customer-facing action needed. Tag any related tickets fw-v1.2 for tracking.
