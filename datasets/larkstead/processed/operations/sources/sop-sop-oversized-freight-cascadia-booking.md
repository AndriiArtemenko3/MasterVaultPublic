---
domain: operations
type: source
title: Sop Oversized Freight Cascadia Booking
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: sop-sop-oversized-freight-cascadia-booking-01
  statement: One carton over the small-parcel carrier's weight or dimension cap takes the whole inbound shipment out of parcel and into LTL.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-02
  statement: This SOP sets the line between LTL and parcel and the steps to book Cascadia Freight (VEN-06) when LTL wins.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-03
  statement: The small-parcel carrier's current caps are 150 lbs and 108 in combined length and girth.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-04
  statement: Cascadia account number is required for the Reno dock.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-05
  statement: 'Count the cartons that stay under cap: 40 cartons or fewer can still ride small-parcel if nothing trips the per-carton cap.'
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-06
  statement: 41 cartons and up requires quoting LTL regardless of individual carton weight.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-07
  statement: Email Cascadia's dispatch desk with carton count, total weight, and the pickup window.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-08
  statement: A quote at 500.00 or more needs Ana's sign-off before booking.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-09
  statement: If the quote reaches 1000.00, it needs Mara's approval.
  confidence: high
  affects: []
- id: sop-sop-oversized-freight-cascadia-booking-10
  statement: Cascadia has no-showed twice this year on bookings left unconfirmed.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/sop/sop-oversized-freight-cascadia-booking.md
provenance_hash: dc7b364b3e1da1d9
---

# Sop Oversized Freight Cascadia Booking

## Summary

SOP: Oversized inbound freight booking, LTL vs. parcel Effective: 2026-02-02 Owner: Dmitri Okafor (DO) Applies to: Operations Systems: Cascadia Freight booking (email/phone), Ledgerly Purpose One carton over the small-parcel carrier's weight or dimension cap takes the whole inbound shipment out of parcel and into LTL.

## Content

SOP: Oversized inbound freight booking, LTL vs. parcel
Effective: 2026-02-02
Owner: Dmitri Okafor (DO)
Applies to: Operations
Systems: Cascadia Freight booking (email/phone), Ledgerly

Purpose
One carton over the small-parcel carrier's weight or dimension cap takes the
whole inbound shipment out of parcel and into LTL. This SOP sets the line
between the two and the steps to book Cascadia Freight (VEN-06) when LTL
wins.

Prerequisites
- Vendor packing list with carton count, per-carton weight, and per-carton
  dimensions
- The small-parcel carrier's current caps on hand: 150 lbs, 108 in combined
  length and girth
- Cascadia account number for the Reno dock

Steps
1. Pull the packing list off the vendor's ship notice. Ostrava (VEN-02) and
   Verdant (VEN-01) both send one before their container clears customs.
2. Check every carton against the parcel cap. One carton over 150 lbs, or
   over the combined-dimension limit, moves the entire shipment to LTL even
   if the rest of the load would have qualified for parcel on its own.
3. Count the cartons that stay under cap. 40 cartons or fewer of heavy-class
   stock, Birch frames or Rowan bases, can still ride small-parcel if nothing
   trips the per-carton cap. 41 cartons and up: quote LTL regardless of
   individual carton weight, the per-unit cost crosses over around there and
   parcel stops being the cheaper option once volume does that much of the
   work for you.
4. LTL applies: email Cascadia's dispatch desk with carton count, total
   weight, and the pickup window. Ask for a firm quote, not a range.
5. Compare the Cascadia quote against a parcel estimate for the same load, if
   one exists. Book the cheaper option unless the delivery date misses a
   committed restock date, in which case book Cascadia regardless of price.
6. A quote at 500.00 or more needs Ana's sign-off before booking, per the
   self-approve threshold in force since 01 Jul 2025. Under that, book
   directly. If the quote reaches 1000.00, it needs Mara too, per the CEO
   approval line.
7. Confirm the booking by phone the day before pickup. Cascadia has no-showed
   twice this year on bookings left unconfirmed.

Escalation
A quote landing more than 15 percent over the last comparable Cascadia
booking goes to Dmitri directly, not booked on the spot. Two missed pickup
windows on one vendor shipment escalate to Mara.

Close-out
Log the carrier, quote amount, and pickup date against the shipment record.
If the vendor caused the delay, packing list wrong or cartons not staged, a
line goes in that vendor's file too, not just the shipment record.

References
- Cascadia Freight (VEN-06), Net-30
- Expense policy: self-approve under 500.00, CEO approval from 1000.00,
  effective 2025-07-01
