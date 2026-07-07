# Template: live chat transcript (Helprise chat widget)

Doc class: CUSTOMER-SUPPORT. Chat transcripts attach to a ticket ID.
Read with `../company.yaml` and `../style-rules.md`.

## Skeleton

```
Chat: HD-<YYYY>-<NNNNN>
Date: <YYYY-MM-DD>
Started: <HH:MM PT>  Ended: <HH:MM PT>
Visitor: <name or "guest">
Agent: <agent full name>
Page: <storefront URL the widget opened on>

[HH:MM] <Visitor first name>: <text>
[HH:MM] <Visitor first name>: <split messages: second thought, correction>
[HH:MM] <Agent first name>: <text per voice card>
[HH:MM] -- <system line: agent joined / visitor is typing gap / visitor left>
<...>
```

## Exemplar (half length)

```
Chat: HD-2025-04536
Date: 2025-08-20
Started: 11:03 PT  Ended: 11:12 PT
Visitor: Maya Brooks
Agent: Celeste Marin
Page: /products/wren-laptop-stand

[11:03] Maya: hi does the wren stand fit a 16 inch laptop
[11:03] Maya: its one of the bigger ones, like 4.7 lbs
[11:04] -- Celeste Marin joined the chat
[11:04] Celeste: Hi Maya. Do you have an order number with us? If not, no
problem, happy to answer pre-sale questions too.
[11:05] Maya: no order yet, deciding
[11:06] Celeste: The Wren laptop stand holds laptops up to 17 inches and
8 lbs, so yours fits with room to spare.
[11:06] Celeste: It is 79.00, and orders of 75.00 and up ship free for
light items (free shipping policy, effective 2024-03-01). Policy page:
/help/orders-returns-warranty
[11:08] Maya: one sec
[11:11] Maya: ok and if it doesnt work with my dock setup i can return it?
[11:12] Celeste: Yes. 30 days from delivery, full refund unused, 10%
restocking fee if opened and not defective (returns policy, effective
2024-01-15).
[11:12] -- Visitor left the chat
```

## Realism notes

- No closing recap and no goodbye ritual. Visitors leave mid-thread; the
  system line is the ending.
- Customers split thoughts across messages and self-correct in a second
  message. Agents write in complete lines.
- Silence is visible: "one sec" then a 3-minute gap in the timestamps.
  Timestamps advance monotonically and unevenly.
- Voice cards hold in chat: Celeste asks for the order number first even
  pre-sale, quotes policy with its effective date, and drops the policy link.
  Jonah in the same chat would write "no worries, gonna check".
- Pre-sales chats carry no order number, and figures quoted match the date:
  this 2025-08 chat says 79.00 (price effective 2024-09-01), 75.00 threshold,
  and 30 days, all correct for 2025-08-20.
- Customer typos per the budget ("doesnt", no caps); agent text stays clean
  unless the voice card allows slippage.
