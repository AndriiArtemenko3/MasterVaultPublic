---
domain: operations
type: source
title: Process B2B Freight Quoting
tags:
- process
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: process
key_claims:
- id: process-process-b2b-freight-quoting-01
  statement: B2B freight quoting process is effective on 2026-04-06.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-02
  statement: Dmitri Okafor owns the B2B freight quoting process.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-03
  statement: The trigger for B2B quoting is a proposal including specific desk, chair, or bundle SKUs.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-04
  statement: Heavy items are flagged for freight quoting, not the flat shipping-zone rate.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-05
  statement: LTL rate is pulled from ParcelPoint's carrier for the delivery zip.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-06
  statement: A dock survey is mandatory for the first delivery to a given address.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-07
  statement: Freight quotes hold for 30 days, matching pricing quote validity.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-08
  statement: Freight appears as its own line, separate from unit price, in quotes.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-09
  statement: Delivery is booked once the order is signed and survey notes are attached.
  confidence: high
  affects: []
- id: process-process-b2b-freight-quoting-10
  statement: Delivery is confirmed by the site contact for closing the process.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/process/process-b2b-freight-quoting.md
provenance_hash: 1d138699efe03fd4
---

# Process B2B Freight Quoting

## Summary

Process: B2B freight quoting Effective: 2026-04-06 Owner: Dmitri Okafor Teams: operations, sales, finance Trigger: a B2B proposal includes desk, chair, or bundle SKUs shipping to a new delivery address Stages | # | stage | owner | system | exit criteria | |---|---|---|---|---| | 1 | weight flag | TA/YT | Pipewell | every SKU checked against weight_class; heavy items flagged for freight quoting, not the flat shipping-zone rate | | 2 | carrier quote | DO | ParcelPoint | LTL rate pulled from ParcelPoint's carrier for the delivery zip; quote logged against the opportunity | | 3 | dock survey | DO/HM | phone, email | for a first delivery to an address, loading dock or freight-elevator access confirmed before the freight line is finalized | | 4 | quote issued | TA/YT | Pipewell | freight appears as its own line, separate from unit price; 30-day validity noted alongside the pricing quote | | 5 | booking | DO | ParcelPoint | delivery window booked once the order is signed; survey notes attached so the carrier arrives with the right equipment | | 6 | close | DO | Helprise | delivery confirmed by the site contact; dock notes filed under the account for the next order to that address | Rules - Heavy items always bill the zone rate under the shipping-zone table in force since 2024-03-01. The free-shipping threshold waives light and medium rates only and never touches freight for desks, chairs, or bundles.

## Content

Process: B2B freight quoting
Effective: 2026-04-06
Owner: Dmitri Okafor   Teams: operations, sales, finance
Trigger: a B2B proposal includes desk, chair, or bundle SKUs shipping to a new delivery address

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | weight flag | TA/YT | Pipewell | every SKU checked against weight_class; heavy items flagged for freight quoting, not the flat shipping-zone rate |
| 2 | carrier quote | DO | ParcelPoint | LTL rate pulled from ParcelPoint's carrier for the delivery zip; quote logged against the opportunity |
| 3 | dock survey | DO/HM | phone, email | for a first delivery to an address, loading dock or freight-elevator access confirmed before the freight line is finalized |
| 4 | quote issued | TA/YT | Pipewell | freight appears as its own line, separate from unit price; 30-day validity noted alongside the pricing quote |
| 5 | booking | DO | ParcelPoint | delivery window booked once the order is signed; survey notes attached so the carrier arrives with the right equipment |
| 6 | close | DO | Helprise | delivery confirmed by the site contact; dock notes filed under the account for the next order to that address |

Rules
- Heavy items always bill the zone rate under the shipping-zone table in force since 2024-03-01. The free-shipping threshold waives light and medium rates only and never touches freight for desks, chairs, or bundles.
- ParcelPoint's carrier invoice for the LTL leg passes straight through at cost. No markup, so freight never quotes worse than ErgoNest's shipping claims.
- A dock survey is mandatory the first time a truck goes to a given address. Repeat orders to the same dock skip the survey and reuse the notes on file.
- Freight quotes hold for 30 days, matching pricing [[quote-validity]], so a signed order inside the window ships at the quoted freight cost even if the carrier's rate has since moved.

Example run
- 2026-02-18: Yuki opens PW-corvidlaw-11seat for Corvid Law Partners, 11 seats, an inquiry off the Boise showroom referral list.
- 2026-02-24: Dmitri requests a carrier quote for 11 Canopy team bundles plus a few loose Heron single arms, to a Boise office neither ParcelPoint nor Larkstead has shipped a pallet to before.
- 2026-03-02: Hank calls the building manager. No loading dock, but the freight elevator clears a standard pallet jack, so the survey passes with a note to bring a hand truck the final stretch.
- 2026-03-04: the carrier quotes 1,240.00 for the LTL leg, one pallet plus loose cartons. Yuki issues the freight line separately from the 12,639.00 bundle total, 11 x 1149.00 at the price in force since the January flip. Both lines hold for 30 days.
- 2026-03-19: Corvid Law signs inside the window. Order #LS64438 books delivery for 2026-04-02 at the quoted freight cost, and the elevator note goes on file for whatever this account orders next.
