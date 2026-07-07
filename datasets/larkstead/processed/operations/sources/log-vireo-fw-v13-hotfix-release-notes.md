---
domain: operations
type: source
title: Vireo Fw V13 Hotfix Release Notes
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: log-vireo-fw-v13-hotfix-release-notes-01
  statement: Vireo firmware v1.3 is released on 2026-02-17.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-02
  statement: Bitgrove Labs publishes the firmware (VEN-03) OTA service.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-03
  statement: The fix targets Meridian PCB rev C, lots LOT-2025-31 and LOT-2025-38.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-04
  statement: The sleep-timer wake interrupt is rescheduled so it no longer collides with the rev C LED driver PWM cycle.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-05
  statement: This closes out the 0.5 Hz sleep-dim flicker introduced in v1.2.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-06
  statement: The flicker issue was reported across 12 support tickets between 2025-12-11 and 2026-01-14.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-07
  statement: Rev C units pinned to the v1.1 rollback image return to the standard OTA channel.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-08
  statement: The pin put in place on 09 Jan is removed as part of this release.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-09
  statement: There are no known issues open against v1.3 at release.
  confidence: high
  affects: []
- id: log-vireo-fw-v13-hotfix-release-notes-10
  statement: Staged OTA rollout for rev C units is 25 percent on 2026-02-17 and 100 percent by 2026-02-21.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/log/vireo-fw-v13-hotfix-release-notes.md
provenance_hash: d45603c3ecb24141
---

# Vireo Fw V13 Hotfix Release Notes

## Summary

Release notes: Vireo firmware v1.3 Released: 2026-02-17 Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor Applies to: all LS-LMP-001 units; the fix targets Meridian PCB rev C, lots LOT-2025-31 and LOT-2025-38 Fixed - Sleep-timer wake interrupt rescheduled so it no longer collides with the rev C LED driver PWM cycle at duty below 20 percent. This closes out the 0.5 Hz sleep-dim flicker introduced in v1.2 and reported across 12 [[support]] tickets between 2025-12-11 and 2026-01-14.

## Content

Release notes: Vireo firmware v1.3
Released: 2026-02-17
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units; the fix targets Meridian PCB rev C, lots LOT-2025-31 and LOT-2025-38

Fixed
- Sleep-timer wake interrupt rescheduled so it no longer collides with the rev C LED driver PWM cycle at duty below 20 percent. This closes out the 0.5 Hz sleep-dim flicker introduced in v1.2 and reported across 12 support tickets between 2025-12-11 and 2026-01-14.

Changed
- Rev C units still pinned to the v1.1 rollback image return to the standard OTA channel; the pin put in place 09 Jan is removed as part of this release.

Known issues
- None open against v1.3 at release.

Rollout
Staged OTA, rev C units first: 25 percent on 2026-02-17, 100 percent by 2026-02-21. Manual path for anyone who wants it sooner: Vireo app, Settings, Check for update.

Support
Sleep-mode dimming is safe to re-enable once a unit is on v1.3. Notify customers on all 12 vireo-flicker tickets that the fix has shipped, including the ones already resolved by the January rollback or the workaround. Affected units keep the 2-year goodwill warranty, scope vireo-v12-affected, effective 2026-01-20, regardless of firmware version going forward.
