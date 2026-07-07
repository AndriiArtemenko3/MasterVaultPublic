Compliance note: Helprise customer data retention and deletion requests
Date: 2026-02-09
Author: Priya Raman
Status: informational
Next review: 2026-08-09

Position
Ticket retention in Helprise is set to 24 months rolling, auto-purge runs the 1st of every month for anything past that window. Checked the config against a handful of test cases 05 Feb 2026: three tickets from Aug 2023 gone as expected, three from Sept 2024 still there, right where they should be given the rolling window. Deletion requests sit outside that automatic clock; a customer can ask sooner and folks process those by hand. Ran it twice in January. Ruben Silva asked for his order history removed on ticket HD-2026-65700, resolved same day, confirmed by email. The second was an internal walk-through with Jonah on what happens when a request touches payment details, since anything tied to Ledgerly routes through Ana before deletion, not straight to Helprise.

Obligations
| obligation | detail | where handled |
|---|---|---|
| ticket retention | 24 months rolling from ticket close, auto-purge on the 1st of each month | Helprise config |
| deletion request handling | customer-initiated, actioned within 5 business days, confirmed by email | Support queue, Priya Raman |
| payment-linked deletion | routed to Ana Petrova before purge when Ledgerly records are involved | Finance/admin |

Open items
- Helprise auto-purge clears the ticket thread but not photo attachments, those sit in a separate media bucket that doesn't purge on the same clock. Owner PR, no fix scheduled yet.
- If a deletion request comes in from someone on a B2B seat account, then it should route through the account contact, not get actioned off the individual's say-so alone. Owner PR, watch for the first one, none so far.
