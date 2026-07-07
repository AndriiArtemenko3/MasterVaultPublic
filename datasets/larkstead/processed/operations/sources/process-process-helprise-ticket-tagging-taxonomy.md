---
domain: operations
type: source
title: Process Helprise Ticket Tagging Taxonomy
tags:
- ticket
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: ticket
key_claims:
- id: process-process-helprise-ticket-tagging-taxonomy-01
  statement: The Helprise ticket tagging process becomes effective on 2026-02-02.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-02
  statement: Priya Raman is the owner of the Helprise ticket tagging process.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-03
  statement: Teams involved in the Helprise ticket tagging process include support, operations, and sales.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-04
  statement: A new Helprise ticket triggers the ticket tagging process and retro-tag sweep.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-05
  statement: Helprise tickets must carry exactly one product tag before the first reply goes out.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-06
  statement: During incident check, Helprise tickets are compared against open incident tags.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-07
  statement: The account match stage checks email domain or account name against open Pipewell records.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-08
  statement: Resolution tags such as repair, replace, refund, info-only, and escalated are set before a ticket is closed.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-09
  statement: Priya performs the retro-tag sweep on the first business day of the month.
  confidence: high
  affects: []
- id: process-process-helprise-ticket-tagging-taxonomy-10
  statement: Priya logs the completeness percentage of tickets during the retro-tag sweep.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/process/process-helprise-ticket-tagging-taxonomy.md
provenance_hash: 4add3cda26ad73ed
---

# Process Helprise Ticket Tagging Taxonomy

## Summary

Process: Helprise ticket tagging taxonomy and retro-tagging Effective: 2026-02-02 Owner: Priya Raman Teams: [[support]], operations, sales Trigger: any new Helprise ticket, plus the first-business-day-of-month retro-tag sweep Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | intake | PR/JB/CM | Helprise | ticket carries exactly one product tag before the first reply goes out | | 2 | incident check | PR | Helprise | ticket compared against open incident tags; matched or left untagged | | 3 | account match | PR/JB/CM | Helprise, Pipewell | email domain or account name checked against open PW- records; account tag applied on a match | | 4 | closure tag | whoever closes it | Helprise | resolution tag set (repair, replace, refund, info-only, escalated) before status flips to closed | | 5 | retro-tag sweep | PR | Helprise | prior month's closed tickets pulled; completeness percentage logged; gaps corrected | | 6 | sign-off | PR | email | sweep total sent to Dmitri and Mara; any cause crossing 3 tickets opens a new incident tag | Rules - Product tags map to SKU family, not the exact SKU: desk, chair, mat, lamp, monitor-arm, footrest, cable, bundle, replacement-part, other. A multi-item ticket takes one tag per line item touched, not one tag per ticket.

## Content

Process: Helprise ticket tagging taxonomy and retro-tagging
Effective: 2026-02-02
Owner: Priya Raman   Teams: support, operations, sales
Trigger: any new Helprise ticket, plus the first-business-day-of-month retro-tag sweep

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | intake | PR/JB/CM | Helprise | ticket carries exactly one product tag before the first reply goes out |
| 2 | incident check | PR | Helprise | ticket compared against open incident tags; matched or left untagged |
| 3 | account match | PR/JB/CM | Helprise, Pipewell | email domain or account name checked against open PW- records; account tag applied on a match |
| 4 | closure tag | whoever closes it | Helprise | resolution tag set (repair, replace, refund, info-only, escalated) before status flips to closed |
| 5 | retro-tag sweep | PR | Helprise | prior month's closed tickets pulled; completeness percentage logged; gaps corrected |
| 6 | sign-off | PR | email | sweep total sent to Dmitri and Mara; any cause crossing 3 tickets opens a new incident tag |

Rules
- Product tags map to SKU family, not the exact SKU: desk, chair, mat, lamp, monitor-arm, footrest, cable, bundle, replacement-part, other. A multi-item ticket takes one tag per line item touched, not one tag per ticket.
- Incident tags exist only while a root cause is open. Priya opens one once three or more tickets land on the same cause inside a rolling 14 days, and retires it the week after the fix ships.
- B2B account tags use the Pipewell opportunity slug directly, for example PW-summit-8seat, so support sees contract terms without leaving Helprise.
- Retro-tagging runs the first business day of the month. A queue under 95% completeness gets a second pass before the sweep closes.
- Tags never come off a closed ticket, only get added. History matters more than a tidy-looking ticket.

Example run
- 2026-01-28: ticket HD-2026-64400 opens from a one-off buyer, Lena Ostrowski, reporting a wobble in the left rear caster of a Rowan chair delivered two weeks earlier. Jonah tags it chair, replacement-part.
- 2026-01-29: Celeste closes two more tickets on the same caster wobble, both from customers on the same delivery week. Priya opens incident tag caster-wobble-lot-2026-03 and back-tags all three.
- 2026-02-02: this process goes into effect the same week, timed to the caster incident on purpose so the taxonomy gets tested live instead of on paper.
- 2026-03-02: retro-tag sweep for February finds 61 of 64 closed tickets fully tagged, a completeness rate of 95.3%. The three gaps are B2B account tags missed on tickets from Summit Physio Group. Priya adds PW-summit-8seat to each and logs the count to Dmitri, who has zero questions about it.
