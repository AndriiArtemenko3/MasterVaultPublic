---
domain: operations
type: source
title: Process Weekly Support Queue Triage
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: process-process-weekly-support-queue-triage-01
  statement: The weekly support queue triage process is effective starting 2024-04-01.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-02
  statement: Priya Raman is the owner of the support queue triage process.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-03
  statement: The aging review stage sorts every open ticket by age.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-04
  statement: Tickets open for more than 3 business days are flagged during the aging review stage.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-05
  statement: Tickets open for more than 5 business days with no reply escalate to Priya.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-06
  statement: Certain ticket complaints escalate on the same day regardless of age.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-07
  statement: The 30-day refund window and 10% restocking fee on opened, non-defective items is effective 2024-01-15.
  confidence: high
  affects:
  - refund-policy
- id: process-process-weekly-support-queue-triage-08
  statement: Only Priya can sign off on swapping the triage seat.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-09
  statement: The Monday review of the queue does not get skipped for a short week or holiday.
  confidence: high
  affects: []
- id: process-process-weekly-support-queue-triage-10
  statement: Queue was clear by Tuesday noon for the week of 2024-06-10.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/process/process-weekly-support-queue-triage.md
provenance_hash: 5b5c324e9e5cd589
---

# Process Weekly Support Queue Triage

## Summary

Process: Weekly [[support]] queue triage Effective: 2024-04-01 Owner: Priya Raman Teams: support Trigger: Monday morning, start of business Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | aging review | PR | Helprise | every open ticket sorted by age; anything past 3 business days flagged | | 2 | tag hygiene | PR | Helprise | product and issue tags checked on flagged tickets; missing or duplicate tags corrected | | 3 | escalation | PR | Helprise | tickets meeting escalation criteria assigned to the right owner the same day | | 4 | redistribution | PR | Helprise | open load rebalanced across Jonah and Celeste | | 5 | close-out | PR | Helprise, email | short note to Dmitri on any pattern across three or more tickets in the week | Rules - Escalates to Priya: any ticket open more than 5 business days with no reply, no matter how it's tagged. - Escalates same day regardless of age: desk wobble, chair tipping, or motor overheating complaints.

## Content

Process: Weekly support queue triage
Effective: 2024-04-01
Owner: Priya Raman   Teams: support
Trigger: Monday morning, start of business

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | aging review | PR | Helprise | every open ticket sorted by age; anything past 3 business days flagged |
| 2 | tag hygiene | PR | Helprise | product and issue tags checked on flagged tickets; missing or duplicate tags corrected |
| 3 | escalation | PR | Helprise | tickets meeting escalation criteria assigned to the right owner the same day |
| 4 | redistribution | PR | Helprise | open load rebalanced across Jonah and Celeste |
| 5 | close-out | PR | Helprise, email | short note to Dmitri on any pattern across three or more tickets in the week |

Rules
- Escalates to Priya: any ticket open more than 5 business days with no reply, no matter how it's tagged.
- Escalates same day regardless of age: desk wobble, chair tipping, or motor overheating complaints. Safety first, queue position second.
- Returns opened this week follow the 30-day refund window and the flat 10% restocking fee on opened, non-defective items, both effective 2024-01-15. No B2B waiver exists yet.
- Folks only swap the triage seat with Priya's sign-off, and the Monday review itself doesn't get skipped for a short week, holiday or not.

Example run (week of 2024-06-10)
Monday's aging review turned up six tickets past three business days: two footrest return requests, one Wren stand assembly question, one Rowan chair caster complaint, and two general shipping-time asks. The caster ticket and one shipping ask were missing a product tag; both corrected before anything else moved.

The caster complaint, open six days, had the customer describing the chair rolling out from under them on hardwood. That one escalated to Priya first thing Monday, and she offered a Rowan caster set replacement under the standard warranty rather than treating it as a return, since the part itself looked like the failure, not the customer's floor. Load got rebalanced too: two tickets moved off Jonah's queue onto Celeste's, since he'd fallen three days behind clearing Friday's backlog before the weekend.

Note to Dmitri: two footrest returns from the same part of town in one week, worth a look at whether the local courier's handling is the actual problem.

Queue clear by Tuesday noon.
