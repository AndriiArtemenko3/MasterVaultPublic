Finance rule: Revenue recognition for staged B2B deliveries
Effective: 2025-08-11
Supersedes: none
Owner: Ana Petrova   Approved by: Mara Voss

Rule
When a B2B contract ships in multiple waves instead of one shipment, revenue recognizes wave by wave at ship confirmation, not at contract signing and not when the customer's deposit clears. Each wave's recognized value is that wave's unit count times the price in force on the ship confirmation date, less any discount tier the account qualifies for. A deposit taken at signing books as a liability, unearned revenue, never as revenue and never as contra-revenue. As each wave ships and Ledgerly logs the ship confirmation, the liability draws down first. Once the liability is exhausted, further wave value books straight to accounts receivable on the account's normal terms.

Scope
Applies to B2B accounts with a written, multi-wave delivery schedule. A single-wave B2B order recognizes the same way DTC does, in full at ship confirmation, so this rule adds nothing there. Deposits under 500.00 fall outside this rule and post as ordinary prepayment instead, since a deposit that small rarely spans more than one wave's value.

Procedure
Tom or Yuki logs the wave schedule in Pipewell against the opportunity once the contract signs, one line per wave with a target ship date. Ana books the deposit as a liability the day funds clear, citing the opportunity ID inline. When a wave gets a ship confirmation, she posts that wave's revenue the same business day if the confirmation lands before 3pm, the next business day if it lands after, and updates the remaining liability balance in the same entry.

Worked example
A hypothetical 12-seat account signs for the Roost executive bundle at 1449.00 per seat, total contract value 17388.00, with a 30% deposit at signing: 5216.40, booked as a liability. Twelve seats is short of the discount tier. No tier discount applies. Wave 1 ships 6 seats on 2025-09-15 and gets ship confirmation: recognized revenue 8694.00. The 5216.40 liability is smaller than that, so it zeroes out entirely and the remaining 3477.60 posts to accounts receivable. Wave 2 ships the other 6 seats on 2025-10-20: recognized revenue 8694.00, invoiced in full since no liability remains.

Change log
- 2025-08-11: first version.
