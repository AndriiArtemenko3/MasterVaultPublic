---
domain: operations
type: source
title: Bitgrove Bug Report Flicker 01
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-report-bitgrove-bug-report-flicker-01-01
  statement: Bug report vireo-v12-sleep-dim-flicker was filed on 2025-12-19 by Dmitri Okafor with Bitgrove Labs.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-02
  statement: The system under report is Vireo firmware v1.2, released on 2025-12-09.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-03
  statement: LS-LMP-001 units built on Meridian PCB rev C flicker at roughly 0.5 Hz during sleep-mode dim on firmware v1.2.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-04
  statement: Flickering appears within 30 seconds when the sleep-mode dim schedule is enabled and brightness is set below 20 percent on firmware v1.2.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-05
  statement: 'The affected population includes Meridian Components PCB rev C across two lots: LOT-2025-31 with 400 units and LOT-2025-38 with 350 units.'
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-06
  statement: There are 750 rev C units total in the field.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-07
  statement: Seven support tickets have been submitted to date, all confirmed on firmware v1.2.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-08
  statement: All tickets are for units purchased in October 2025 or later.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-09
  statement: A customer-side workaround to stop flicker is disabling sleep-mode dimming in the Vireo app.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-10
  statement: Rev B units on firmware v1.2 do not reproduce the flicker under identical conditions.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-11
  statement: Cobalt Dental Group reported flicker on both of its Vireo add-on units on the same day as the bug report was filed.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-12
  statement: A root-cause analysis on the rev C interaction is requested while a rollback option to v1.1 for rev C serials is also requested.
  confidence: high
  affects: []
- id: bug-report-bitgrove-bug-report-flicker-01-13
  statement: The requested rollback option should be a signed image pinned to the OTA channel for the affected serial range only.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-report/bitgrove-bug-report-flicker-01.md
provenance_hash: 3f59d466985923dd
---

# Bitgrove Bug Report Flicker 01

## Summary

Bug report: vireo-v12-sleep-dim-flicker Date: 2025-12-19 Filed by: Dmitri Okafor (DO) Filed with: Bitgrove Labs (VEN-03) System under report: Vireo firmware v1.2, released 2025-12-09 Related tickets: 7 to date -- HD-2025-05402, HD-2025-05417, HD-2025-05433, HD-2025-05461, HD-2025-05478, HD-2025-05490, HD-2025-05511 (first HD-2025-05402, 2025-12-11) Summary LS-LMP-001 units built on Meridian PCB rev C flicker at roughly 0.5 Hz during sleep-mode dim on firmware v1.2. Reproduction steps 1.

## Content

Bug report: vireo-v12-sleep-dim-flicker
Date: 2025-12-19
Filed by: Dmitri Okafor (DO)
Filed with: Bitgrove Labs (VEN-03)
System under report: Vireo firmware v1.2, released 2025-12-09
Related tickets: 7 to date -- HD-2025-05402, HD-2025-05417, HD-2025-05433, HD-2025-05461, HD-2025-05478, HD-2025-05490, HD-2025-05511 (first HD-2025-05402, 2025-12-11)

Summary
LS-LMP-001 units built on Meridian PCB rev C flicker at roughly 0.5 Hz during sleep-mode dim on firmware v1.2.

Reproduction steps
1. LS-LMP-001 unit with Meridian PCB rev C, firmware v1.2 installed.
2. Enable the sleep-mode dim schedule.
3. Set brightness below 20 percent.
4. Flicker at roughly 0.5 Hz appears within 30 seconds.

Affected population
Meridian Components (VEN-07) PCB rev C across two lots: LOT-2025-31, 400 units, received 22 Sep; LOT-2025-38, 350 units, received 17 Nov. 750 rev C units total in the field.

Evidence
7 support tickets to date, all on units purchased October 2025 or later, all confirmed on firmware v1.2. Customer-side workaround (disabling sleep-mode dimming in the Vireo app) stops the flicker on every unit tested. Rev B units on the same firmware v1.2 do not reproduce the flicker under identical dim-schedule and brightness conditions, which points at a rev C hardware interaction rather than a pure firmware defect.

One B2B account, Cobalt Dental Group, reported flicker on both of its two Vireo add-on units the same day this report was filed (HD-2025-05511), which is our first multi-unit-per-account confirmation.

Ask
Root-cause analysis on the rev C interaction, and a rollback option to v1.1 for rev C serials while that analysis is underway. We'd like this as a signed image we can pin to the OTA channel for the affected serial range only, not a full fleet rollback, since rev B units show no issue on v1.2.
