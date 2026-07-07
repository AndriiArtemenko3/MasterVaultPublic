SOP: Inventory cycle count
Effective: 2024-11-04
Owner: Dmitri Okafor (DO)
Applies to: warehouse crew, operations
Systems: Ledgerly, ParcelPoint portal

Purpose
Keep the on-hand quantities in Ledgerly close enough to the physical shelf that a stockout or a phantom unit never reaches a customer order. A-items get a physical count every week. Everything else gets counted once a month.

Prerequisites
- A fresh Ledgerly on-hand export, pulled the same morning as the count, not a cached report from Friday
- The current A-item list, re-set at the start of each quarter by Dmitri
- ParcelPoint portal login for reconciling heavy SKUs held at the Reno warehouse

Steps
1. Pull the Ledgerly on-hand report every Monday before the floor opens. As of 04 Nov the A-item list is LS-MAT-001-CHL, LS-MAT-001-SND, LS-CHR-001-BLK, and LS-DSK-001-48, the four highest-velocity SKUs.
2. Count the physical shelf quantity for each A-item held in the Portland backroom. Light SKUs count by case; the chair counts unit by unit, no estimating from carton stacks.
3. For LS-DSK-001-48, which ships heavy through the ParcelPoint Reno warehouse rather than sitting in Portland, pull the ParcelPoint portal on-hand figure instead of a physical count and set it against Ledgerly's number for that SKU.
4. Record the counted or reconciled figure next to the Ledgerly figure for each of the 4 A-items. If the two numbers match, log it as clean and move to the next SKU.
5. If a single SKU shows a variance over 2% of its Ledgerly quantity, recount that SKU once before touching anything else. A 2% variance on a 40-unit shelf count is 1 unit, so round down when the math lands on a fraction.
6. If the recount still doesn't clear the 2% threshold, do not adjust Ledgerly yourself. Flag it to Dmitri same day with both counts written down.
7. On the first working day of the month, run the same process against every remaining SKU in the catalog, A-items included. This is the full-shelf count and it takes the whole floor, plan for it rather than squeezing it between other tasks.
8. Log the monthly count date and the number of SKUs that cleared without a recount. 26 of 28 SKUs clearing clean is a normal month, and more than 3 recounts in a single pass usually points to a receiving or picking error somewhere further up the chain rather than a mistake in the counting itself, so treat repeat recounts as a signal worth chasing down.

Escalation
Any SKU failing a second recount, or any month where more than 5 SKUs need a recount, goes to Dmitri the same day the count closes. He decides whether Ledgerly gets adjusted or the discrepancy gets logged open pending a root cause. Never guess on this one.

Close-out
Weekly A-item counts close with a one-line note in the shared count log: date, SKUs checked, variances found, recounts run. The monthly full count closes with the same note plus a total clean-SKU count for the month.

References
- Inbound receiving inspection SOP, effective 2024-02-12, for how quantities enter Ledgerly in the first place
- ParcelPoint Fulfillment (VEN-04), Reno warehouse, for heavy-SKU on-hand reconciliation
