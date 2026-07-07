---
domain: operations
type: decision
title: Vireo firmware v1.2 flicker — rollback pin or wait for a hotfix
tags:
- vireo
- firmware
- bitgrove
status: draft
created: '2025-12-19'
updated: '2026-02-17'
decision_status: closed
outcome: Larkstead pinned rev C serials to a signed v1.1 rollback image on 2026-01-09 as an interim measure, then removed the pin once Bitgrove shipped the root-cause hotfix in v1.3 on 2026-02-17. No known issues remained open at the v1.3 release.
---

## Question

Should Larkstead pin affected Vireo lamps back to firmware v1.1 while waiting for Bitgrove Labs to ship a root-cause fix, or leave units on v1.2 until the hotfix lands?

## Options

**Option A: Leave rev C units on v1.2 and wait for Bitgrove's fix.**
No engineering work on Larkstead's side, but customers keep experiencing a visible 0.5 Hz flicker during sleep-mode dim with no fix date committed.

**Option B: Disable the sleep-mode dimming schedule as a customer-facing workaround.**
Stops the flicker for affected units immediately, but customers lose a feature they use and did not ask to give up.

**Option C: Request a signed v1.1 rollback image pinned to the OTA channel for the affected serial range only, then unpin once the real fix ships.**
Restores known-good behavior for exactly the affected population without touching unaffected units or waiting on Bitgrove's release schedule.

## Evidence

- The defect was isolated to Meridian PCB rev C units on firmware v1.2, flickering at roughly 0.5 Hz during sleep-mode dim below 20% brightness (bug-report-bitgrove-bug-report-flicker-01-03, bug-report-bitgrove-bug-report-flicker-01-04).
- The affected population was bounded and known: LOT-2025-31 (400 units) and LOT-2025-38 (350 units) (bug-report-bitgrove-bug-report-flicker-01-05).
- Six flicker tickets landed in one week against a firmware release only ten days old, on units all bought October 2025 or later (chat-log-vireo-flicker-escalation-chat-01, chat-log-vireo-flicker-escalation-chat-02).
- Customers explicitly did not want to lose the night-dimming feature as a workaround (chat-log-vireo-flicker-escalation-chat-05), which weighed against Option B.
- Dmitri requested both a root-cause analysis and a rollback option to v1.1 for rev C serials specifically, not a blanket rollback (bug-report-bitgrove-bug-report-flicker-01-12).
- The rollback request specified a signed image pinned to the OTA channel for the affected serial range only, not a full-fleet downgrade (bug-report-bitgrove-bug-report-flicker-01-13).
- Bitgrove's v1.3 hotfix rescheduled the sleep-timer wake interrupt so it no longer collides with the rev C LED driver PWM cycle, closing the root cause rather than working around it (log-vireo-fw-v13-hotfix-release-notes-04, log-vireo-fw-v13-hotfix-release-notes-05).
- The flicker issue spanned 12 support tickets between 2025-12-11 and 2026-01-14, and the v1.1 pin was removed as part of the v1.3 rollout with a staged push to 100% by 2026-02-21 (log-vireo-fw-v13-hotfix-release-notes-06, log-vireo-fw-v13-hotfix-release-notes-07, log-vireo-fw-v13-hotfix-release-notes-10).

## Criteria

Time to relief for affected customers, blast radius of any fix (targeted vs. fleet-wide), and whether the interim measure creates a second migration once the real fix ships.

## Recommendation

Option C. A targeted rollback pin gets affected units back to known-good behavior within days instead of waiting on Bitgrove's release calendar, and un-pinning later is a single clean step once v1.3 lands, unlike a feature-disabling workaround that would need its own reversal.

## What would change my mind

If Bitgrove could not produce a signed v1.1 image on short notice, the workaround (Option B) would become the only fast option despite the feature loss.

## Next action

None outstanding. sop-sop-vireo-ota-release-hardware-matrix now requires every future Vireo release to run the full bench matrix against both rev C and rev D before any staged push, closing the gap that let v1.2 ship unqualified against rev C.
