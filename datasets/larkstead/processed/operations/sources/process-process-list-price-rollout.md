---
domain: operations
type: source
title: Process List Price Rollout
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: process-process-list-price-rollout-01
  statement: The list-price change rollout is effective on 2026-01-26.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-02
  statement: Ana Petrova owns the list-price change rollout process.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-03
  statement: The list-price change rollout involves the finance, sales, and operations teams.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-04
  statement: The cost review triggers on any vendor notice above a 5% unit cost move or each November.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-05
  statement: Proposals sent before the effective date are honored at the old price if they are within the 30-day quote window.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-06
  statement: The table update gets filed at least one full business day ahead of the intended flip.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-07
  statement: New prices are confirmed live at the end of the process.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-08
  statement: Bundles are repriced as a set and set by Ana Petrova directly instead of recalculating live from components.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-09
  statement: The process is written up once the first live cycle closes clean.
  confidence: high
  affects: []
- id: process-process-list-price-rollout-10
  statement: Cost review fields are generated for the next cycle after the close stage.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/process/process-list-price-rollout.md
provenance_hash: 19fd0c988fc65e1f
---

# Process List Price Rollout

## Summary

Process: List-price change rollout Effective: 2026-01-26 Owner: Ana Petrova Teams: finance, sales, operations Trigger: a vendor cost increase notice, or the annual pricing review each November Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | cost review | AP/DO | Ledgerly | vendor cost delta documented against current unit_cost; margin impact calculated per SKU | | 2 | price decision | MV | email | new price and effective date approved in writing | | 3 | table update | RL | Shopstack | price_history entry added; live price flips at the start of the effective date | | 4 | quote-honoring check | TA/YT | Pipewell | open B2B proposals sent before the effective date reviewed against their 30-day quote window | | 5 | comms hold | SG | Mailloft | no discount or promo send scheduled inside the 5 business days around the flip | | 6 | close | AP | Ledgerly | new prices confirmed live; cost review filed for the next cycle | Rules - Ray only works Shopstack tickets Tuesdays and Thursdays per his contract, so the table update gets filed at least one full business day ahead of the intended flip, never same-day. - A proposal sent before the effective date and still inside its 30-day quote window is honored at the old price through the order stage, even after the table has flipped for new orders.

## Content

Process: List-price change rollout
Effective: 2026-01-26
Owner: Ana Petrova   Teams: finance, sales, operations
Trigger: a vendor cost increase notice, or the annual pricing review each November

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | cost review | AP/DO | Ledgerly | vendor cost delta documented against current unit_cost; margin impact calculated per SKU |
| 2 | price decision | MV | email | new price and effective date approved in writing |
| 3 | table update | RL | Shopstack | price_history entry added; live price flips at the start of the effective date |
| 4 | quote-honoring check | TA/YT | Pipewell | open B2B proposals sent before the effective date reviewed against their 30-day quote window |
| 5 | comms hold | SG | Mailloft | no discount or promo send scheduled inside the 5 business days around the flip |
| 6 | close | AP | Ledgerly | new prices confirmed live; cost review filed for the next cycle |

Rules
- Ray only works Shopstack tickets Tuesdays and Thursdays per his contract, so the table update gets filed at least one full business day ahead of the intended flip, never same-day.
- A proposal sent before the effective date and still inside its 30-day quote window is honored at the old price through the order stage, even after the table has flipped for new orders.
- Cost review triggers on any single vendor notice above a 5% unit cost move, or automatically each November regardless of vendor notices that quarter.
- Bundles reprice as a set. AP sets the bundle list price directly rather than recalculating it live from components, then reconciles unit_cost against the component sum plus the 6.00 ParcelPoint kitting fee afterward.

Example run
- 2025-11-14: Ostrava Metalworks notifies a steel surcharge on frame and base stock. Dmitri logs the cost delta. Margin tightens.
- 2025-12-02: Mara approves new prices effective 2026-01-15: the Birch desks up 30.00 across all three lengths, the Rowan chairs up 20.00, the Heron dual arm and Vireo lamp up 10.00 each, and all three bundles up 50.00.
- 2026-01-08: Ray schedules the Shopstack change for the 2026-01-15 flip during his Thursday window.
- Meanwhile Tom had opened PW-fernbrook-14seat on 2025-11-02 and sent Fernbrook Design Studio a proposal on 2025-12-20 for 14 seats of the Canopy team bundle at the old 1099.00, no volume tier at that seat count. The 30-day window ran to 2026-01-19.
- 2026-01-14: Fernbrook signs. Order #LS64405 goes in one day ahead of the flip at the quoted price, 14 x 1099.00 is 15,386.00, no tax on the Bend, Oregon delivery address, total 15,386.00 due Net-30.
- 2026-01-26: this process is written up once the first live cycle closes clean, so the next vendor notice has a documented path instead of Ana improvising over email each time.
