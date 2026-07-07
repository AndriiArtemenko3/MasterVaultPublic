---
domain: customer-support
type: source
title: Sl5 Ticket Hd 2026 00219
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: ticket-sl5-ticket-hd-2026-00219-01
  statement: 'Alma Reyes ordered a desk mat and a cable sleeve set on January 15, order #LS32320.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00219-02
  statement: 'The merchandise total for order #LS32320 is 78.00.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00219-03
  statement: 'Alma Reyes was charged 5.95 for shipping on order #LS32320.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00219-04
  statement: The free shipping threshold rose from 75.00 to 99.00 on January 15, 2026.
  confidence: high
  affects:
  - shipping-threshold
- id: ticket-sl5-ticket-hd-2026-00219-05
  statement: Orders totaling 78.00 do not qualify for free shipping under the current policy.
  confidence: high
  affects:
  - refund-policy
- id: ticket-sl5-ticket-hd-2026-00219-06
  statement: The help page incorrectly states that orders over 75.00 ship free.
  confidence: medium
  affects:
  - shipping-policy
- id: ticket-sl5-ticket-hd-2026-00219-07
  statement: The help center has not been updated to reflect the new shipping threshold.
  confidence: medium
  affects:
  - shipping-policy
- id: ticket-sl5-ticket-hd-2026-00219-08
  statement: 'There has been no carrier scan for order #LS32320, tied to backlog from the fulfillment switch.'
  confidence: high
  affects: []
- id: ticket-sl5-ticket-hd-2026-00219-09
  statement: 'Movement of order #LS32320 is estimated by January 20, 2026.'
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/customer-support/ticket/sl5-ticket-hd-2026-00219.md
provenance_hash: 96eb26b7c61dec0b
---

# Sl5 Ticket Hd 2026 00219

## Summary

Ticket: HD-2026-00219 Subject: your website says free shipping over 75?? Requester: Alma Reyes <alma.reyes@example.com> Order: #LS32320 Status: open Assignee: Celeste Marin Priority: normal Tags: parcelpoint-cutover, shipping-threshold, no-carrier-scan Channel: email --- Message 1 (customer) --- 2026-01-19 15:12 PT I ordered a desk mat and a cable sleeve set on the 15th, order #LS32320, came to 78.00 and got charged 5.95 shipping on top.

## Content

Ticket: HD-2026-00219
Subject: your website says free shipping over 75??
Requester: Alma Reyes <alma.reyes@example.com>
Order: #LS32320
Status: open
Assignee: Celeste Marin
Priority: normal
Tags: parcelpoint-cutover, shipping-threshold, no-carrier-scan
Channel: email

--- Message 1 (customer) --- 2026-01-19 15:12 PT
I ordered a desk mat and a cable sleeve set on the 15th, order #LS32320, came to 78.00 and got charged 5.95 shipping on top. Your help page says orders over 75.00 ship free, mine was 78.00. Can you refund the 5.95? Nothing's moved since, no carrier scan, its been 4 days.

--- Internal note (CM) --- 2026-01-19 16:03 PT
#LS32320, Alma Reyes, placed 2026-01-15. 1x LS-MAT-001-CHL 59.00 + 1x LS-ACC-004 19.00 = 78.00 merchandise, both light. threshold that day is 99.00 (rose 2026-01-15), 78.00 doesn't qualify, 5.95 US-1 light shipping correctly charged. OR no tax. total 83.95. quoting the old FAQ, 75.00 hasn't been true since the 15th, help center hasn't been updated. no carrier scan, transition-week backlog.

--- Message 2 (agent, public reply) --- 2026-01-19 16:31 PT
Hi Alma, straight answer below.

Order #LS32320 was placed 2026-01-15, the day our free-shipping threshold rose from 75.00 to 99.00. Merchandise total was 78.00, so the 5.95 shipping charge is correct under the current threshold, not a mistake.

The help-center page you're reading still shows the old 75.00 figure. It hasn't caught up yet, sorry for the confusion. Current policy is here: /help/shipping-policy-2026-01.

On tracking: no carrier scan yet, tied to backlog from this month's fulfillment switch. Estimate movement by 2026-01-20.

Celeste
Larkstead Support

--- Message 3 (customer) --- 2026-01-19 17:48 PT
Ok, annoying that the help page is wrong but at least I know now. Update me if tracking still hasnt moved tomorrow
