---
domain: operations
type: source
title: Integration Guide Mailloft Abandoned Cart
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: integration-guide-integration-guide-mailloft-abandoned-cart-01
  statement: Shopstack fires a cart.abandoned webhook once a cart sits untouched for 60 minutes with at least one item and a captured email address.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-02
  statement: Sofia holds the Mailloft campaign editor and the suppression list.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-03
  statement: Ray holds the Shopstack webhook config and the Mailloft API key.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-04
  statement: A new contact is created if none exists in Mailloft for a cart.email.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-05
  statement: Up to 3 items render in the email; more than that collapses to 'and X more'.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-06
  statement: The free-shipping reminder line is shown only above 75.00.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-07
  statement: B2B accounts are tagged and excluded from the abandoned-cart flow.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-08
  statement: The first email, named Drizzle, goes out 1 hour after cart.abandoned_at.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-09
  statement: A second email, Chinook, goes out at 24 hours after the first.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-10
  statement: Sofia reviews the weekly send log every Friday.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-11
  statement: Sofia catches personal email B2B buyers by eye when she reviews the weekly send log.
  confidence: medium
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-12
  statement: Drizzle sends twice in cases of a retry on a slow webhook post.
  confidence: medium
  affects: []
- id: integration-guide-integration-guide-mailloft-abandoned-cart-13
  statement: Ray changes the webhook and delay configuration only on Tuesdays and Thursdays.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/integration-guide/integration-guide-mailloft-abandoned-cart.md
provenance_hash: 48d7d3ef6d18aa4b
---

# Integration Guide Mailloft Abandoned Cart

## Summary

Integration guide: Shopstack cart abandonment to Mailloft Systems: Shopstack (storefront) -> Mailloft (email marketing) Owner: Sofia Grieg (SG) Maintainer: Ray Lindqvist (RL) Written: 2024-07-22 Schedule Event-driven. Shopstack fires a cart.abandoned webhook once a cart sits untouched for 60 minutes with at least one item and a captured email address; Mailloft queues the first send from there.

## Content

Integration guide: Shopstack cart abandonment to Mailloft
Systems: Shopstack (storefront) -> Mailloft (email marketing)
Owner: Sofia Grieg (SG)   Maintainer: Ray Lindqvist (RL)
Written: 2024-07-22

Schedule
Event-driven. Shopstack fires a cart.abandoned webhook once a cart sits untouched for 60 minutes with at least one item and a captured email address; Mailloft queues the first send from there. Nothing runs on a nightly clock.

Access
Sofia holds the Mailloft campaign editor and the suppression list. Ray holds the Shopstack webhook config and the Mailloft API key the webhook posts through, no campaign edit access, so he can break the plumbing but never the copy.

Field mapping
| Shopstack field | Mailloft destination | note |
|---|---|---|
| cart.email | contact.email | matched against an existing contact record, a new contact is created if none exists |
| cart.line_items | event.cart_items | up to 3 items render in the email; more than that collapses to "and X more" |
| cart.value | event.cart_value | drives the free-shipping reminder line, shown only above 75.00 |
| cart.abandoned_at | event.trigger_time | starts the send-delay clock, see below |
| customer.account_type | suppression.tag | B2B accounts are tagged and excluded, see the suppression rule |

Suppression rule
B2B contacts never enter the abandoned-cart flow. Company buyers work off a quote and a Pipewell opportunity, not a cart, so an abandoned cart tied to a known B2B email suppresses the whole sequence at the webhook, before Mailloft ever queues a send. The tag comes from whether the email matches a contact already flagged account_type: b2b in Pipewell. A B2B buyer using a personal email Pipewell doesn't recognize still gets the sequence, and Sofia catches those by eye when she reviews the weekly send log, adding the address to the suppression list by hand.

Send-delay configuration
The first email, named Drizzle, goes out 1 hour after cart.abandoned_at. If the cart is still unconverted, a second email, Chinook, goes out at 24 hours. No third touch. Both delays are configured in Mailloft against event.trigger_time rather than wall-clock send time, so a cart abandoned at 11pm still gets its Drizzle email around midnight instead of waiting for a daytime slot.

Failure modes
- B2B contact receives the sequence anyway, usually the personal-email case above. Detect it via a reply asking why a company buyer got a discount-flavored cart email; fix it by adding the address to suppression, and if it looks like a pattern, ask Tom or Yuki to get the work email into Pipewell instead.
- Drizzle sends twice. Happened once in 2024, when a retry on a slow webhook post queued the same cart.abandoned_at event a second time. Detect it via Mailloft's duplicate-send report, which Sofia checks weekly; the fix is deduplication on Ray's side, keyed to cart.id plus trigger_time so a retry can't queue a second event for the same cart.
- Free-shipping line shows on a cart under 75.00, usually a rounding or currency-field mismatch on cart.value. Detect it when a customer replies confused about a shipping claim that doesn't match their cart; fix it by checking the raw cart.value against the Shopstack order record before the next send window closes it out.

Change control
Ray changes the webhook and delay configuration only on Tuesdays and Thursdays, tested against a sandbox cart first. Sofia owns the email copy and the delay values themselves and can adjust send timing without Ray, so long as the underlying event fields stay the same.

Verification
Sofia reviews the weekly send log for suppression misses and duplicate sends every Friday, the same day Ana runs her own reconciliation on the Ledgerly side.
