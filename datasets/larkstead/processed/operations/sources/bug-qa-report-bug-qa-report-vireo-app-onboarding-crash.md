---
domain: operations
type: source
title: Bug Qa Report Vireo App Onboarding Crash
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-01
  statement: The Vireo companion app crashes on the onboarding pairing screen for customers on phone OS build 18.3.2.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-02
  statement: The app version 2.4.1 was released on 2025-03-04.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-03
  statement: Bitgrove's crash dashboard shows 41 crash events tied to the onboarding screen since the 2.4.1 release.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-04
  statement: 38 crash events were from build 18.3.2 and 3 were from build 18.3.1.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-05
  statement: Five customers reported a lamp bought between 2025-03-05 and 2025-03-17.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-06
  statement: The app does not reproduce the crash on build 18.2.x.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-07
  statement: The crash did not reproduce on the build 18.4 beta phone tested on 2025-03-13.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-08
  statement: Jonah paired the same model of lamp cleanly on his own phone, on build 18.2.4.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-09
  statement: Support needs a hotfix date for customers before 2025-04-01.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-vireo-app-onboarding-crash-10
  statement: A target date for the build 2.4.2 patch is set before 2025-04-01 to clear the backlog.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-qa-report/bug-qa-report-vireo-app-onboarding-crash.md
provenance_hash: 09ff746534f0d917
---

# Bug Qa Report Vireo App Onboarding Crash

## Summary

Bug report: vireo-companion-onboarding-crash-osbuild1832 Date: 2025-03-19 Filed by: Priya Raman (PR) Filed with: Bitgrove Labs (VEN-03) System under report: Vireo companion app, version 2.4.1 (phone OS build 18.3.2 only) Related tickets: 5 to date -- HD-2025-63800, HD-2025-63801, HD-2025-63802, HD-2025-63803, HD-2025-63804 (first HD-2025-63800, 2025-03-11) Summary The Vireo companion app crashes on the onboarding pairing screen for customers on phone OS build 18.3.2, starting with app version 2.4.1, released 2025-03-04. Reproduction steps 1.

## Content

Bug report: vireo-companion-onboarding-crash-osbuild1832
Date: 2025-03-19
Filed by: Priya Raman (PR)
Filed with: Bitgrove Labs (VEN-03)
System under report: Vireo companion app, version 2.4.1 (phone OS build 18.3.2 only)
Related tickets: 5 to date -- HD-2025-63800, HD-2025-63801, HD-2025-63802, HD-2025-63803, HD-2025-63804 (first HD-2025-63800, 2025-03-11)

Summary
The Vireo companion app crashes on the onboarding pairing screen for customers on phone OS build 18.3.2, starting with app version 2.4.1, released 2025-03-04.

Reproduction steps
1. Update the Vireo companion app to version 2.4.1 on a phone running OS build 18.3.2.
2. Unbox a new LS-LMP-001 lamp and open the app for first-time setup.
3. Tap "Find my lamp" on the onboarding screen. The app closes to the home screen within 2 to 3 seconds, before Bluetooth pairing finishes.

Affected population
Bitgrove's crash dashboard shows 41 crash events tied to the onboarding screen since the 2.4.1 release on 2025-03-04, 38 of them on build 18.3.2 and 3 on build 18.3.1. Five customers have opened tickets so far, all reporting a lamp bought between 2025-03-05 and 2025-03-17.

Evidence
Five tickets to date. Each describes the same crash within seconds of the "Find my lamp" tap. It does not reproduce on build 18.2.x, and it did not reproduce on the build 18.4 beta phone Priya tested against in the showroom on 2025-03-13. Jonah paired the same model of lamp cleanly on his own phone, on build 18.2.4, while troubleshooting HD-2025-63801 by phone, so the fault sits with the newer build rather than with any one lamp unit.

Ask
Folks in support need a hotfix date we can promise customers. We're asking for a build 2.4.2 patch scoped to the onboarding screen only, no firmware change required, since the lamp's own firmware never touches this crash path. A target before 2025-04-01 would clear the backlog before the two showroom pre-orders on this month's list finish their pairing calls.
