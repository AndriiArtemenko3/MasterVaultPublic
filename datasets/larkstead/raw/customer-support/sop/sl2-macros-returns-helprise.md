SOP: Returns macros RET-01 and RET-02
Effective: 2024-02-09
Owner: Priya Raman (PR)
Applies to: support agents
Systems: Helprise, ParcelPoint portal

Purpose
Give agents the two standard macro replies for return requests, and the
lookup step that has to happen before either one goes out.

Prerequisites
- Agent has Helprise access to the macro library
- Agent has ParcelPoint read access to pull delivery dates

Steps
1. Confirm the delivery date in ParcelPoint before quoting eligibility.
   Never quote from the order date or the ship date.
2. Delivery date 30 days or less from today: send macro RET-01. Reply
   text reads "within 30 days of delivery" and includes the restocking
   fee line: 10% on opened, non-defective returns.
3. Delivery date over 30 days from today: send macro RET-02. Reply text
   reads "outside our 30-day return window."
4. Log the delivery date and the day count in the internal note either
   way, so the next agent on the ticket doesn't have to look it up
   again.

Escalation
Customer disputes a RET-02 denial: hold the ticket and loop in Priya
before offering any exception.

Close-out
Nothing beyond the ticket status change. No separate log.

References
- Returns and refunds policy, effective 2024-01-15
