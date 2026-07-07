Finance rule: AP invoice intake and three-way match
Effective: 2024-04-15
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
No vendor invoice posts to Ledgerly until three records agree: the purchase order, the receiving log entry, and the invoice itself. SKU or line description, quantity, and unit price must match across all three, zero tolerance on quantity and a 2% tolerance on price to absorb rounding on freight-inclusive lines. An invoice that clears the match posts the same business day. One that doesn't sits as an exception until it's resolved.

Scope
Covers every purchase order raised against a vendor on the standing vendor list, from a container load off Cascadia Freight to a single carton reorder with GreenCrate Packaging. Standing invoices with no purchase order behind them, rent, insurance, the legal retainer, are exempt and post on Ana's review alone, since there's nothing to three-way match against.

Procedure
Hank logs receiving quantities the day a shipment lands: carrier, dock time, unit count. Ana pulls the matching PO once the vendor invoice arrives and holds all three side by side, line by line. If everything ties out, Ledgerly gets the entry within 1 business day of the invoice date. If it doesn't, the invoice goes into the exception queue and Ana emails whoever can close the gap, vendor contact or warehouse, with the PO number and the discrepancy stated in units and dollars, never rounded. Exceptions worth 250.00 or more in variance copy Mara too, since that crosses the self-approve line under the standing expense policy.

Worked example
An Ostrava Metalworks shipment of 40 Birch desk frames for the 60 in model (LS-DSK-001-60), landed at 248.00 each, arrives 14 Mar 2024; Hank's receiving log shows 40 units in. The PO calls for 40 units at 248.00, 9920.00 total. Invoice INV-OSM-2024-755 bills 9920.00 flat. All three agree, so it posts that day. Compare the GreenCrate Packaging carton order from the same week: PO for 3000 desk cartons, receiving log shows 2950 landed, invoice bills the full 3000. Fifty cartons short. That's a quantity mismatch, and quantity has no tolerance, so it waits in the exception queue instead of going straight into Ledgerly.

Change log
- 2024-04-15: first written version.
