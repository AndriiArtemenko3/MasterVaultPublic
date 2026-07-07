Process: Purchase order issuance
Effective: 2024-06-01
Owner: Dmitri Okafor   Teams: operations, finance
Trigger: on-hand count for a SKU falls below its reorder point

Stages
| # | stage | owner | system | exit criteria |
|---|---|---|---|---|
| 1 | reorder trigger | HM | ParcelPoint portal | SKU on-hand below reorder point, flagged same day |
| 2 | draft order | DO | email | quantity and landed cost calculated from unit cost, sent to the vendor contact |
| 3 | approval | DO/MV | email | signature at the correct band lands before the order goes out |
| 4 | vendor confirmation | DO | email | vendor confirms unit price, ready date, and lead time inside the confirmation window |
| 5 | freight booking | DO | email | Cascadia Freight books drayage once a ready date is confirmed |
| 6 | receiving | HM | receiving log | goods checked in against the order, lot number assigned, discrepancies logged same day |

Rules
- Reorder points aren't fixed. 07 Jun: reorder points below 40 units get reviewed monthly against the last 90 days of sell-through, so the trigger point moves with demand instead of sitting still all year.
- Self-approve under 250.00, manager band 250.00 to 999.99, CEO sign-off from 1000.00, effective 2024-01-15. A full container order from Ostrava or Verdant clears 1000.00 on nearly every run, so Mara signs almost every reorder that goes out this year.
- Vendor confirmation window: 5 business days from the draft order date. No confirmation by day 5, Dmitri calls the vendor contact directly instead of waiting on email.
- Payment terms follow the vendor record: Net-45 for Ostrava Metalworks and Verdant Textiles, Net-30 for Meridian Components.

Example run (LS-DSK-001-48 reorder, June 2024)
03 Jun: Hank flags 36 units on hand against the 50-unit reorder point.
04 Jun: Dmitri drafts an order for 200 units at 212.00 landed cost each, 42,400.00 total, well past the CEO band.
05 Jun: Mara signs off same afternoon; order goes to Ostrava Metalworks.
10 Jun: Ostrava confirms price and a ready date of 22 Jul, inside the five-day window.
24 Jul: Cascadia books the container out of the port.
30 Aug: shipment received at the ParcelPoint dock, checked in against the order, logged as LOT-2024-09. No discrepancies on the count.

Reorder point resets once the new count posts to the portal.
