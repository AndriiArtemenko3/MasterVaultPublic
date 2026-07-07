# Larkstead corpus style rules

Binding on every writer agent. Read together with `company.yaml` in this
directory: that file supplies every ID, price, date, name, and policy version.
This file governs how the prose around those facts is written. Mechanical
checkers grep against `id_grammars` and `banned_strings`; a human reviewer
reads for register. Both must pass.

## Register by document type

| Doc type | Register |
|---|---|
| Support ticket, customer side | Everyday consumer email or chat. Contractions, uneven punctuation, occasional typo (see typo budget). Customers misremember details: in roughly 1 of 6 tickets the customer gives a wrong or partial SKU or order number and the agent corrects it. |
| Support ticket, agent side | Follows the agent's voice card. Quotes the policy version in force on the ticket date, not the newest one. Internal notes on the ticket are terser than the public reply. |
| Chat transcript | Turn-by-turn with timestamps. Interruptions, split messages, "one sec" gaps. No closing recap. |
| Internal email | Terse, first names, no formal salutations. Threads drift off-topic and that is fine. |
| B2B sales email | Seat counts and dollar figures appear early. Tom is brisk, Yuki is formal; see the voice cards. |
| CRM note (Pipewell) | Fragments allowed. Always names the opportunity as `PW-<slug>` and ends with a next step plus a date. |
| Invoice / PO | Zero typos. Every line arithmetically exact: qty times unit price, discount, then tax per `tax_table`. Terms match the vendor's `payment_terms`. |
| Policy document | Zero typos. Header carries the effective date. Versions never merge; a revision is a new dated document. |
| Meeting notes | Bullet fragments with an owner and a date on every action item. Disagreements get recorded, not resolved by the note-taker. |
| Marketing email (Mailloft) | Short. Concrete claims with numbers and dates. No superlative chains, no urgency theatre. |
| Warehouse / ops log | Lowercase acceptable. Counts, lot numbers, carrier names, dock times. |

## Banned vocabulary and patterns

Never use, in any document type: delve, robust, seamless, comprehensive,
leverage (as a verb), tapestry, furthermore, moreover, additionally (as a
sentence opener), "not only X but also Y".

Also banned:

- Bullet lists with bold inline headers in customer-facing prose. Write sentences.
- Em-dash parenthetical asides. Use a comma, a colon, or a second sentence.
- "I hope this email finds you well" and every variant of it.
- Closing zoom-outs ("all of this points to...", "at the end of the day...").
- Restating the previous sentence in different words.

The `banned_strings` list in `company.yaml` is matched case-insensitively as a
substring. Avoid innocent collisions: "striped" contains a banned string, so
write "lined" or name the color instead.

## Sentence-length variance

Each block of roughly 300 words needs at least one sentence over 30 words and
at least one under 6 words. No paragraph of three or more sentences of nearly
equal length. Short after long. Real writing is uneven; keep it uneven.

## Typo budget

Customer messages carry about 1 typo per 80 words: a dropped letter, swapped
letters, a missing apostrophe, a lowercase i. Typos never land inside SKUs,
IDs, dollar amounts, or dates. Staff typos appear only where a voice card
allows them (Jonah in chat, Hank in chat) at roughly 1 per 200 words, internal
surfaces only. Policies, contracts, invoices, and POs carry zero typos.

## Staff voices

Every internal document has a named author, and the prose must follow that
author's voice card in `company.yaml` closely enough that a reader who knows
the cards could identify the author blind. Do not average voices across a
thread; two people in one thread should sound like two people.

## Endings

Tickets and transcripts end where real ones end: a hanging last message, a
short system line ("ticket closed", "marked resolved"), or nothing. Never a
paragraph summarizing what was decided. Meeting notes end on the action list.

## Numbers

Concrete over vague. "9 of 140 units" beats "several units"; "5 business days"
beats "shortly". Percentages state their base. Money is exact to the cent in
any operational document. Dates are absolute (2026-02-17); relative dates
("last Tuesday") appear only inside quoted customer speech.

## Date and price consistency

The price on any document is the `price_history` entry in force on that
document's date. Policy citations use the version in force on that date. A
2025-10 ticket cites the 30-day refund window; a 2025-12 ticket cites 45 days.
Cross-document contradictions across a policy boundary are the point of the
dataset; contradictions inside a single document are defects.
