---
domain: customer-support
type: decision
title: Extend the standard return window from 30 to 45 days
tags:
- return-policy
- refund-policy
status: draft
created: '2025-11-01'
updated: '2026-01-12'
decision_status: closed
outcome: Return window extended from 30 to 45 days, permanent policy as of 2026-01-12, applied uniformly across every product category and year-round.
---

## Question

Should Larkstead extend the standard return window beyond 30 days from delivery?

## Options

**Option A: Keep the 30-day window as-is.**
No process change, but the window had already produced customer friction on cases sitting just outside it.

**Option B: Extend to 45 days for large furniture only, since assembly delays push those returns closer to the deadline.**
Targets the category most likely to need it but adds a second window agents have to track per SKU.

**Option C: Extend to 45 days across every category, year-round.**
Simplest rule for agents to apply and quote, at the cost of a longer average return liability window.

## Evidence

- The original policy set a flat 30-day window from the carrier-confirmed delivery date, applied the same way across every category (policy-sl2-policy-returns-v1-01).
- At least one ticket shows a customer denied a return specifically because the request landed outside the 30-day window (ticket-sl2-ticket-hd-2024-02311-05), the kind of edge case the extension was meant to close.
- The revised policy sets a 45-day window from delivery (policy-sl2-policy-returns-v2-01), made permanent as of 2026-01-12 (policy-sl2-policy-returns-v2-04), applied the same way year-round across every category (policy-sl2-policy-returns-v2-05, policy-sl2-policy-returns-v2-06).
- A ticket under the new policy confirms the 45-day window working as intended: an order with days to spare inside the new deadline where it would have been denied under the old one (ticket-sl2-ticket-hd-2026-00187-04, ticket-sl2-ticket-hd-2026-00187-05).
- One residual risk surfaced in rollout: the standard macro text for RET-02 still read "outside our 30-day return window" after the policy changed, a stale reference agents needed to catch (sop-sl2-macros-returns-helprise-10).

## Criteria

Reduction in denied-return friction, uniformity of the rule across categories, and how much the change increases the average window during which a sale can be reversed.

## Recommendation

Option C, which is what shipped. A single uniform window is the only version of this change a support agent can quote from memory without checking a table, and the 15 extra days closes the exact edge case that produced customer complaints under the old rule.

## What would change my mind

A material rise in the return rate itself, as opposed to the timing of returns, would argue for tightening the window back down regardless of when the return lands.

## Next action

None outstanding. Confirm every returns macro references 45 days, not 30, since sop-sl2-macros-returns-helprise-10 shows at least one stale reference survived the initial rollout.
