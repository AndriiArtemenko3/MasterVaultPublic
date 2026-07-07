# Template: saved reply / macro (Helprise)

Doc class: CUSTOMER-SUPPORT. Lives in the Helprise macro library.
Read with `../company.yaml` and `../style-rules.md`. Macro codes follow
`<CAT>-<NN>` (RET = returns, SHP = shipping, WTY = warranty, DEF = defect,
WIS = where-is-my-order, DMG = damaged-in-transit, B2B = B2B routing,
PKP = showroom pickup).

## Skeleton

```
Macro: <CAT>-<NN>
Name: <short internal name>
Created: <YYYY-MM-DD> by <staff full name>
Last edited: <YYYY-MM-DD> by <staff full name>   <- omit if never edited
Trigger tags: <helprise tags this macro pairs with>

## Body

Hi {{customer.first_name}},

<reply text with Helprise placeholders: {{customer.first_name}},
{{ticket.id}}, {{order.id}}, {{agent.first_name}}. States the policy
figures baked in at creation time.>

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- <when to send, when not to>
- <required edits before sending>
```

## Exemplar (half length)

```
Macro: RET-01
Name: standard return, in window
Created: 2024-02-09 by Priya Raman
Trigger tags: returns

## Body

Hi {{customer.first_name}},

Happy to help with your return. You are within our 30-day return window,
so here is how it works: keep the original packaging if you still have
it, and we will email a prepaid label for small items. Unused items get
a full refund; opened, non-defective items carry a 10% restocking fee.
Your refund lands within 5 business days of the warehouse receiving the
item back.

Reply here with your order number if it is not already on this ticket
({{ticket.id}}) and we will get the label out.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Confirm the delivery date in ParcelPoint before quoting eligibility.
- Do not send to B2B contacts; escalate those to Tom Aldridge.
- Defective items skip this macro entirely; no fee applies.
```

## Realism notes

- Macros go stale by design. SL2 canon: RET-01 and RET-02 still say 30
  days after the 2025-11-03 change and are never corrected. Do not add a
  "Last edited" line the storyline says never happened.
- Placeholders appear only in the library document. A sent ticket shows the
  rendered text with the real first name; a raw `{{customer.first_name}}`
  inside a ticket thread is a defect.
- Macro register is slightly stiffer than any one agent's voice, because it
  is written to be sent by all of them. The sending agent's personality shows
  in the sentence they add above or below the macro body.
- Usage notes are internal and terse. Rules about who not to send to (B2B
  escalation) live here, not in the customer-visible body.
- Figures in the body match the policy version in force on the Created (or
  Last edited) date. A macro created 2024-02-09 says 30 days and 10%.
