---
domain: operations
type: source
source_type: sop
title: Warehouse Returns Processing SOP
tags: [warehouse, returns]
status: processed
created: 2026-03-20
updated: 2026-03-22
key_claims:
  - id: sop-returns-01
    statement: Returned items must be inspected and graded within two business days of arriving at the warehouse.
    confidence: high
    affects: [refund-window]
  - id: sop-returns-02
    statement: Items graded resalable go back to sellable stock the same day they are inspected.
    confidence: medium
    affects: []
---

# Warehouse Returns Processing SOP

## Intake

Scan every inbound return against its RMA number at the dock. Unmatched parcels go to the holding shelf and get flagged to support within four hours.

## Grading

Inspect and grade each item within two business days of arrival. Grades are resalable, refurbish, or scrap. Resalable items return to sellable stock the same day; refurbish items queue for the Tuesday rework session.

## Refund Trigger

Grading completion is what releases the customer refund, so a backlog here directly delays refunds inside the promised window.
