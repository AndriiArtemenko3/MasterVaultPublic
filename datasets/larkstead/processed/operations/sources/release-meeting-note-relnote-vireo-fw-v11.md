---
domain: operations
type: source
title: Relnote Vireo Fw V11
tags:
- call-transcript
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: call-transcript
key_claims:
- id: release-meeting-note-relnote-vireo-fw-v11-01
  statement: Vireo firmware v1.1 was released on 2025-06-10.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-02
  statement: The publisher of the release is Bitgrove Labs (VEN-03) OTA service.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-03
  statement: The update applies to all LS-LMP-001 units.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-04
  statement: The scene recall fixed issue involved the wrong saved color temperature being applied.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-05
  statement: The sleep-schedule feature now includes a start and end time per day of the week.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-06
  statement: Two new preset scenes called reading and video call were added.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-07
  statement: The app onboarding screen was shortened by one step.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-08
  statement: There are no known issues open at release.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-09
  statement: The staged OTA rollout percentages are 25 percent on June 10, 60 percent on June 12, and 100 percent on June 15.
  confidence: high
  affects: []
- id: release-meeting-note-relnote-vireo-fw-v11-10
  statement: No hardware revisions are called out in this release.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/release-meeting-note/relnote-vireo-fw-v11.md
provenance_hash: 715af38e2a188b63
---

# Relnote Vireo Fw V11

## Summary

Release notes: Vireo firmware v1.1 Released: 2025-06-10 Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor Applies to: all LS-LMP-001 units Fixed - Scene recall occasionally applied the wrong saved color temperature when two scenes were triggered inside the same second from the app's quick-switch row. Scene lookup now queues instead of racing.

## Content

Release notes: Vireo firmware v1.1
Released: 2025-06-10
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units

Fixed
- Scene recall occasionally applied the wrong saved color temperature when two scenes were triggered inside the same second from the app's quick-switch row. Scene lookup now queues instead of racing.

Changed
- Sleep-schedule feature added: a start and end time per day of the week instead of one daily timer, set per scene rather than per device.
- Two new preset scenes added: reading and video call, both adjustable after the first save.
- App onboarding screen shortened by one step now that the schedule setup walks new users through the same flow.

Known issues
- None open at release.

Rollout
Staged OTA: 25 percent 10 Jun, 60 percent 12 Jun, 100 percent 15 Jun. Manual path: Vireo app, Settings, Check for update. No action needed for units already on v1.0.

Support
No hardware revisions called out in this release; the update applies the same way across every Vireo unit in the field. Tag related tickets fw-v1.1 so Dmitri can track adoption against the rollout percentages.
