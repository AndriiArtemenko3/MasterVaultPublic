---
domain: customer-support
type: decision
title: Raise the free-shipping threshold from 75.00 to 99.00
tags:
- free-shipping
- shipping-policy
status: draft
created: '2025-11-15'
updated: '2026-01-15'
decision_status: closed
outcome: Threshold raised from 75.00 to 99.00, effective 2026-01-15, for light and medium items only. Rollout lagged on the public help page, which kept quoting 75.00 for several weeks and drew customer complaints.
---

## Question

Should Larkstead raise the free-shipping threshold above the 75.00 mark it had held since March 2024?

## Options

**Option A: Leave the threshold at 75.00.**
No customer-facing change, but does not address rising per-parcel shipping cost on light and medium items.

**Option B: Raise the threshold to 99.00, light and medium items only.**
Meaningful lift in average order value needed to qualify, heavy items still excluded as before.

**Option C: Raise the threshold and extend free shipping to heavy items too.**
Biggest customer-facing win but absorbs shipping cost on the category where per-parcel rates are highest.

## Evidence

- The prior threshold was 75.00, effective from 2024-03-01, covering light and medium items only (faq-sl5-shipping-faq-2024-01, faq-sl5-shipping-faq-2024-02, faq-sl5-shipping-faq-2024-03).
- The new policy sets the threshold at 99.00, effective 2026-01-15, with orders placed before that date grandfathered at the old 75.00 mark (policy-sl5-shipping-policy-2026-01-10, policy-sl5-shipping-policy-2026-01-02).
- Free shipping continues to exclude heavy items under the new policy, same as the old one (policy-sl5-shipping-policy-2026-01-04, policy-sl5-shipping-policy-2026-01-05).
- Zone US-1 rates when free shipping doesn't apply run 5.95 light, 12.95 medium, 49.00 heavy (policy-sl5-shipping-policy-2026-01-08), the cost baseline the new threshold is measured against.
- Rollout produced a real gap: a ticket shows an order at 78.00, which cleared the old threshold but not the new one, with the customer pointing out the help page still said 75.00 (ticket-sl5-ticket-hd-2026-00219-05, ticket-sl5-ticket-hd-2026-00219-06). The help center had not been updated to match the new policy at the time of that ticket (ticket-sl5-ticket-hd-2026-00219-07).

## Criteria

Lift in average order value at checkout, exposure on per-parcel shipping cost, and how cleanly the change can be communicated across every customer-facing surface before it takes effect.

## Recommendation

Option B, which is what shipped. Raising the threshold on light and medium items captures most of the cost benefit without touching the heavy-item category where per-parcel cost is already highest and margin is thinnest.

## What would change my mind

If average order value at checkout did not move materially toward the new threshold within a quarter, the change would not be paying for the shipping cost it was meant to offset, and Option A or a smaller increase would need reconsidering.

## Next action

Audit every public-facing page that states the shipping threshold and confirm none still read 75.00; the ticket-sl5-ticket-hd-2026-00219 gap shows this did not happen cleanly on the first pass.
