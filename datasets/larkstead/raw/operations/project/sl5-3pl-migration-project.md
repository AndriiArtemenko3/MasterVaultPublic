Project: 3PL cutover to ParcelPoint, full outbound fulfillment
Owner: Dmitri Okafor (DO), co-owner Hank Morrow (HM)
Opened: 2025-08-11   Target close: 2026-02-11
Related: VEN-04, VEN-06, Amendment No. 1 to the ParcelPoint master services agreement

Goal
Move all outbound parcel fulfillment off the Portland backroom and onto ParcelPoint by 2026-01-12, with 96% of orders shipped within 2 business days measured over the 30 days following cutover.

Order volume roughly doubled year over year against 2024, and the backroom is at capacity: one packing bench, two people on a good day, no room to add a third. That's the whole reason this project exists rather than a headcount fix.

Plan
- Phase 1, contract and rate card: closed 2025-07-28. Signed amendment sets pick-pack at 2.85 per order plus 0.55 per additional item, kitting at 6.00 per kit, and pallet storage at 22.50 per pallet-position per month.
- Phase 2, Shopstack-ParcelPoint integration build: 2025-09-02 to 2025-11-25, built by Ray Lindqvist on his Tuesday/Thursday contract days. Order feed, field mapping, bundle explosion logic, sandboxed against test order #LS90001.
- Phase 3, inventory transfer: 2025-11-17 to 2026-01-08. 14 pallets of finished goods and 96 cartons of replacement parts move via Cascadia Freight (VEN-06) in staged loads, timed to avoid the holiday return peak.
- Phase 4, parallel run: 2025-12-01 to 2025-12-19. Backroom keeps shipping while the ParcelPoint feed runs in shadow mode against the same order stream, output compared daily.
- Phase 5, cutover week: starts 2026-01-12. Shopstack order feed flips live, backroom parcel shipping stops for good.

Cutover lands after the holiday returns peak on purpose. Hank flagged the backroom as underwater in December most years; running the switch during that peak would stack two failure modes at once, so Phase 5 starts the second week of January instead, once the returns volume has settled back down.

Risks
- Bundle SKUs sit at zero on-hand at ParcelPoint if the explosion rule doesn't reach production before cutover: fallback is a manual override list Hank's team checks each morning during Phase 5.
- Inventory transfer runs behind schedule during Phase 3: fallback is a second Cascadia load added to the December weeks rather than compressing the January dates.
- Carrier re-manifest fails on orders transferred mid-transit: fallback is a same-day manual re-manifest queue at ParcelPoint, owned jointly by DO and HM during cutover week.
- Ray is off-schedule on a non-contract day when a mapping bug surfaces: fallback is Dmitri holds a rollback to the prior mapping version until Ray's next Tuesday or Thursday.

Log
11 Aug: kickoff with Hank. Phase 1 closed same week as the signed amendment; Phase 2 kickoff set for 02 Sep with Ray. 14 pallets and 96 cartons confirmed as the Phase 3 transfer scope with Hank's team.
