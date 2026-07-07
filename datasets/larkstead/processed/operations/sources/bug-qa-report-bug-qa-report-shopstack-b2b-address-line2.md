---
domain: operations
type: source
title: Bug Qa Report Shopstack B2B Address Line2
tags:
- bug-report
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: bug-report
key_claims:
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-01
  statement: The Shopstack B2B intake form drops address_line2 on submissions where the company name runs 40 characters or longer.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-02
  statement: On 2025-05-12, Ray's field-map audit pulled 60 B2B submissions from the past 90 days.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-03
  statement: Every one of the 14 submissions with a company name 40 characters or longer lost address_line2 in Pipewell.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-04
  statement: None of the other 46 submissions had the same issue with address_line2 in Pipewell.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-05
  statement: A delivery meant for a suite address landed at the building's ground-floor dock with no suite number on the label.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-06
  statement: The first ticket, HD-2025-63811, was opened on 2025-05-06.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-07
  statement: The second ticket, HD-2025-63814, was opened following another unrelated lead's paperwork hitting the same gap.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-08
  statement: Short company names with a populated address_line2 never reproduce the drop in Pipewell.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-09
  statement: Pipewell accepts populated address_line2 correctly on every short-name submission that Ray pulled.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-10
  statement: A fix to the field map is requested before Juniper Cowork's phased buildout starts.
  confidence: high
  affects: []
- id: bug-qa-report-bug-qa-report-shopstack-b2b-address-line2-11
  statement: The target date for the field map to go live is 2025-05-23.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/bug-qa-report/bug-qa-report-shopstack-b2b-address-line2.md
provenance_hash: ee8f6f25bb0129c3
---

# Bug Qa Report Shopstack B2B Address Line2

## Summary

Bug report: b2b-form-address-line2-drop Date: 2025-05-14 Filed by: Tom Aldridge (TA) Filed with: Ray Lindqvist (RL), internal System under report: Shopstack B2B intake form, field map to Pipewell Related tickets: 2 to date -- HD-2025-63811, HD-2025-63814 (first HD-2025-63811, 2025-05-06) Summary The Shopstack B2B intake form drops address_line2 on submissions where the company name runs 40 characters or longer, and the missing suite number never reaches Pipewell. Reproduction steps 1.

## Content

Bug report: b2b-form-address-line2-drop
Date: 2025-05-14
Filed by: Tom Aldridge (TA)
Filed with: Ray Lindqvist (RL), internal
System under report: Shopstack B2B intake form, field map to Pipewell
Related tickets: 2 to date -- HD-2025-63811, HD-2025-63814 (first HD-2025-63811, 2025-05-06)

Summary
The Shopstack B2B intake form drops address_line2 on submissions where the company name runs 40 characters or longer, and the missing suite number never reaches Pipewell.

Reproduction steps
1. Submit the B2B intake form with a company name 40 characters or longer.
2. Include a suite or unit number in address_line2.
3. Check the resulting Pipewell lead record. address_line2 arrives blank on every long-name submission, even though Shopstack's own confirmation email to the customer shows the suite number correctly.

Affected population
Ray's field-map audit on 2025-05-12 pulled 60 B2B submissions from the past 90 days. Every one of the 14 submissions with a company name 40 characters or longer lost address_line2 in Pipewell. None of the other 46 did.

Evidence
Two tickets to date. A delivery meant for a suite address landed at the building's ground-floor dock with no suite number on the label, which opened HD-2025-63811 on 2025-05-06. A second, unrelated lead's paperwork hit the same gap the following week, HD-2025-63814. Short company names with a populated address_line2 never reproduce the drop, which points at a field-concatenation limit somewhere in the field map rather than a blank-field bug in Pipewell itself, since Pipewell accepts populated address_line2 correctly on every short-name submission Ray pulled.

Ask
A fix to the field map before Juniper Cowork's phased buildout starts submitting suite-numbered addresses floor by floor. Ray, Tuesday works if the patch is ready by then. Fair enough? Target: field map live by 2025-05-23, checked against the same 14-submission sample that failed on 2025-05-12.
