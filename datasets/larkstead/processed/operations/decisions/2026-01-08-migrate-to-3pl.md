---
domain: operations
type: decision
title: Full outbound cutover from the Portland backroom to ParcelPoint
tags:
- fulfillment
- parcelpoint
- 3pl-cutover
status: draft
created: '2025-12-15'
updated: '2026-01-08'
decision_status: closed
outcome: Cutover executed on 2026-01-08 as planned. Final inventory transferred on the Thursday Cascadia load, full pallet count confirmed at ParcelPoint's dock, and the revert plan stayed on standby rather than firing.
---

## Question

Should Larkstead complete a full outbound fulfillment cutover from the Portland backroom to ParcelPoint's Reno warehouse?

## Options

**Option A: Stay with the Portland backroom and add temporary staff during peaks.**
Known process, no integration risk, but does not fix the underlying capacity ceiling.

**Option B: Run a partial cutover, keeping B2B fulfillment in Portland and moving only B2C volume.**
Lower blast radius but leaves two fulfillment paths to maintain and reconcile.

**Option C: Full outbound cutover to ParcelPoint on a fixed date with a defined revert trigger.**
Highest short-term integration risk, but resolves the capacity problem in one motion instead of two.

## Evidence

- The Portland backroom missed its promise date on 28 of 296 orders in the two weeks before Christmas 2024, a 9.5% late-ship rate, against 2.2% the week before volume spiked (postmortem-holiday-backroom-capacity-postmortem-03, postmortem-holiday-backroom-capacity-postmortem-04).
- Hank Morrow flagged the volume climb and requested a temporary packer mid-crunch, a stopgap rather than a fix (postmortem-holiday-backroom-capacity-postmortem-06).
- None of the 28 late orders were lost or damaged, so the backroom's failure mode was capacity, not quality (postmortem-holiday-backroom-capacity-postmortem-08).
- Pre-cutover QA on the ParcelPoint sandbox webhook found a real integration bug: throttled runs produced a 6.7% duplicate-order rate with no idempotency key on the payload (bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-06). The fix (an idempotency key keyed to Shopstack's order ID) was requested before go-live (bug-qa-report-bug-qa-report-parcelpoint-sandbox-dup-0d303691-12).
- The cutover checklist set an explicit revert trigger: if ParcelPoint's order-acknowledgment rate drops below 95%, revert the Shopstack webhook to the backroom queue within 4 hours (checklist-sl5-cutover-checklist-14).
- Dmitri Okafor and Hank Morrow both held the revert switch, and the rollback plan stayed on standby through the run (checklist-sl5-cutover-checklist-13, checklist-sl5-cutover-checklist-15).
- Final inventory transfer loaded on the Thursday Cascadia freight run and the full pallet count was confirmed at ParcelPoint's dock the same day (checklist-sl5-cutover-checklist-05, checklist-sl5-cutover-checklist-06).

## Criteria

Capacity headroom for the next peak season, integration risk on the Shopstack-to-ParcelPoint webhook, and whether a revert path exists if the acknowledgment rate falls short.

## Recommendation

Option C. The backroom's failure mode is a hard capacity ceiling that a temporary packer only delays past one more peak. The sandbox duplicate-order bug is fixable pre-launch, and a 95%-acknowledgment revert trigger with two named owners bounds the downside to four hours of exposure.

## What would change my mind

An acknowledgment rate under 95% sustained past the 4-hour window, or a duplicate-order rate on live traffic anywhere near the 6.7% sandbox figure once the idempotency key ships.

## Next action

None outstanding; monitor the acknowledgment-rate dashboard through the first full quarter live and retire the revert-standby posture only after that quarter closes clean.
