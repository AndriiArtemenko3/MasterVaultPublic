---
domain: internal-admin
type: decision
title: Helprise data-retention terms at the scheduled 2026-08-09 review
tags:
- helprise
- data-retention
- vendor-review
status: draft
created: '2026-05-01'
updated: '2026-05-01'
decision_status: open
review_date: '2026-08-09'
---

## Question

Should Larkstead renew Helprise's current service and data-retention terms as-is at the scheduled 2026-08-09 review, or renegotiate for stronger automation given rising ticket volume?

## Options

**Option A: Renew the current terms unchanged.**
No disruption to existing macros, tagging, and retention rules, but does not address the growing response-time strain the support team is already carrying on the current tooling.

**Option B: Renegotiate with Helprise for added automation and reporting capacity at the same retention terms.**
Keeps the compliance posture already in place while asking the existing vendor to carry more of the growing ticket load.

**Option C: Open a formal evaluation of alternative platforms before the 2026-08-09 review.**
No corpus evidence points to a specific alternative worth naming; this option is listed for completeness but is not grounded in anything beyond the general case for periodic vendor review.

## Evidence

- Ticket retention in Helprise runs 24 months rolling with auto-purge on the 1st of every month (compliance-note-compliance-note-helprise-data-retention-01), consistent with the customer-facing data-handling policy's own 24-month figure (policy-policy-support-data-handling-v1-04).
- Auto-purge clears the ticket thread but not photo attachments, which sit in a separate media bucket on a different clock (compliance-note-compliance-note-helprise-data-retention-06, -07). This is a real gap in the current setup, not a renegotiation wish list item.
- Deletion requests from B2B seat accounts must route through the account contact rather than the individual requester (compliance-note-compliance-note-helprise-data-retention-09), an added handling step every renewal conversation needs to preserve.
- The next scheduled review for this policy is 2026-08-09 (compliance-note-compliance-note-helprise-data-retention-10), which sets the natural decision point for this file's review_date.
- The same 2026 priorities memo that constrains support headcount also commits to holding response times steady as ticket volume climbs (memo-org-memo-org-2026-planning-priorities-07), which puts pressure on the tooling layer if headcount is not the lever being pulled.
- August 2025 first-response time already ran 11 business hours (csat-batch-csat-2025-08-61245-11hr-response-06), a data point for whether the current tool configuration is keeping pace with volume, separate from whether staffing is.

## Criteria

Whether the photo-attachment retention gap needs a contractual fix or a workflow fix, whether added automation capacity is available at the current price tier, and how far Helprise's tooling can absorb rising volume before staffing becomes the only remaining lever.

## Recommendation

No recommendation yet; this decision stays open until the scheduled review. Option B is the likely direction on current evidence: the compliance terms are working as designed and do not need to change, but the response-time data argues for asking the vendor about automation rather than renewing on autopilot.

## What would change my mind

If a formal evaluation surfaced a specific competing platform with materially better automation at comparable cost, that would move the decision toward Option C; nothing in the current corpus supports that case yet.

## Next action

Bring the photo-attachment retention gap and the response-time data to the 2026-08-09 review explicitly rather than treating it as a routine renewal.
