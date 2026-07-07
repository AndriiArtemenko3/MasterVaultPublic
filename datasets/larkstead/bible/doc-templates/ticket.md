# Template: support ticket (Helprise)

Doc class: CUSTOMER-SUPPORT. System of record: Helprise.
Read with `../company.yaml` and `../style-rules.md`. Ticket, order, and lot IDs
that belong to a storyline are allocated in `../storylines/*.yaml`; never mint
an ID a storyline already owns.

## Skeleton

```
Ticket: HD-<YYYY>-<NNNNN>
Subject: <customer-written subject, sentence case, typos allowed>
Requester: <Full Name> <mailbox@example.com>
Order: #LS<NNNNN>                <- omit when pre-sales; agent asks for it
Status: open | pending | solved | closed
Assignee: <agent full name>
Priority: low | normal | high
Tags: <kebab-case, e.g. lot-2025-14, returns, vireo-flicker>
Channel: email | web-form | chat-followup

--- Message 1 (customer) --- <YYYY-MM-DD HH:MM PT>
<customer prose, everyday register, per typo budget>

--- Internal note (<agent initials>) --- <YYYY-MM-DD HH:MM PT>
<terse fragments; lookups, corrections, plan>

--- Message 2 (agent, public reply) --- <YYYY-MM-DD HH:MM PT>
<reply per the agent's voice card; cites the policy version in force
on the ticket date, not the newest one>
<agent first name>
Larkstead Support

--- Message 3 (customer) --- <YYYY-MM-DD HH:MM PT>
<...thread continues, timestamps strictly increasing...>

--- System --- <YYYY-MM-DD HH:MM PT>
Status changed to solved by <agent full name>.
```

## Exemplar (half length)

```
Ticket: HD-2025-00612
Subject: monitor arm missing the clamp?
Requester: Ivy Chen <ivy.chen@example.com>
Order: #LS20960
Status: closed
Assignee: Jonah Beck
Priority: normal
Tags: missing-part, heron-arm
Channel: email

--- Message 1 (customer) --- 2025-02-11 08:47 PT
hi, my dual monitor arm showed up yesterday but theres no desk clamp in
the box. order #LS20690 i think. do you send the clamp seperately or do
i have to return the whole thing?

--- Internal note (JB) --- 2025-02-11 09:58 PT
no #LS20690 in Shopstack. email matches #LS20960, 1x LS-ARM-001-DBL
placed 2025-02-03. clamp kit not in carton tho, pick error at ParcelPoint.
sending LS-ACC-013 free, 16.00, under self-approve.

--- Message 2 (agent, public reply) --- 2025-02-11 10:15 PT
So sorry about this, Ivy, that clamp should have been in the carton. One
quick note, I couldn't find #LS20690 on our side, but your order #LS20960
from 2025-02-03 has the dual Heron arm on it, so I'm working from that one.
No need to return anything, and again I'm really sorry for the hassle.
We're sending a Heron desk-clamp kit (LS-ACC-013) at no charge. It ships
from the warehouse today and you'll have tracking by tonight.
Jonah
Larkstead Support

--- Message 3 (customer) --- 2025-02-11 12:02 PT
oh yeah 20960, my bad. thanks!

--- System --- 2025-02-13 16:20 PT
Status changed to solved by Jonah Beck.
```

## Realism notes

- Timestamps are strictly monotonic down the thread, and reply gaps are
  human: minutes to hours during business hours, overnight silences.
- In roughly 1 of 6 tickets the customer gives a wrong or partial order
  number or SKU. The agent corrects it in the public reply, politely, and
  the internal note shows the lookup. Typos never land inside the corrected
  IDs themselves.
- Public reply and internal note are two registers from the same person.
  Jonah over-apologizes to the customer and writes "tho" internally; Celeste
  quotes policy with its effective date; Priya restates the problem first.
- The policy cited is the version in force on the ticket date. A 2025-02
  ticket says 30-day window; a 2025-12 ticket says 45. Cross-ticket
  contradiction across a policy boundary is intended.
- Tickets end where real ones end: a hanging customer message, a one-line
  system event, or nothing. No recap of what was decided.
- Money in an agent reply is exact and consistent with company.yaml prices
  on that date; a free part still gets its dollar value in the internal note
  because approval thresholds apply to it.
