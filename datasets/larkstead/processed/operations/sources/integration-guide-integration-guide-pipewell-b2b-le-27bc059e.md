---
domain: operations
type: source
title: Integration Guide Pipewell B2B Lead Capture
tags:
- integration-guide
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: integration-guide
key_claims:
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-01
  statement: The B2B contact form on the Shopstack site posts straight to Pipewell immediately after submission.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-02
  statement: Tom Aldridge and Yuki Tanaka hold Pipewell edit access on opportunities.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-03
  statement: Ray Lindqvist holds the Shopstack form config and the Pipewell API key.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-04
  statement: The opportunity naming format is PW-<company-slug>-<seat count>seat.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-05
  statement: New leads are split by segment after Yuki joined in August.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-06
  statement: Architecture, legal, and coworking leads route to Tom.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-07
  statement: Healthcare, financial services, and insurance leads route to Yuki.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-08
  statement: The form has no segment field, so routing relies on Tom or Yuki's judgment.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-09
  statement: Ray owns the technical mapping, changed only on Tuesdays and Thursdays.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-10
  statement: Tom reconciles week's form submissions against new Pipewell opportunities every Monday morning.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-11
  statement: A prospect who fills out the form twice creates two opportunities with the same company slug.
  confidence: high
  affects: []
- id: integration-guide-integration-guide-pipewell-b2b-le-27bc059e-12
  statement: Tom checks form-submission count against Pipewell's new-opportunity count weekly.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/integration-guide/integration-guide-pipewell-b2b-lead-capture.md
provenance_hash: d74bb7a9b7f9d188
---

# Integration Guide Pipewell B2B Lead Capture

## Summary

Integration guide: Shopstack B2B contact form to Pipewell Systems: Shopstack (storefront) -> Pipewell (CRM) Owner: Tom Aldridge (TA) Maintainer: Ray Lindqvist (RL) Written: 2025-09-15 Schedule Real time. The B2B contact form on the Shopstack site posts straight to Pipewell the moment a prospect submits it, no queue and no nightly batch, so a form filled out at midnight shows up in Pipewell before Tom's coffee is cold.

## Content

Integration guide: Shopstack B2B contact form to Pipewell
Systems: Shopstack (storefront) -> Pipewell (CRM)
Owner: Tom Aldridge (TA)   Maintainer: Ray Lindqvist (RL)
Written: 2025-09-15

Schedule
Real time. The B2B contact form on the Shopstack site posts straight to Pipewell the moment a prospect submits it, no queue and no nightly batch, so a form filled out at midnight shows up in Pipewell before Tom's coffee is cold.

Access
Tom and Yuki Tanaka both hold Pipewell edit access on opportunities. Every other Larkstead login sees Pipewell read-only, including Priya's, in case a lead ever needs a support hand-off. Ray holds the Shopstack form config and the Pipewell API key the form posts through. He does not hold a Pipewell seat himself.

Field mapping
| Shopstack form field | Pipewell destination | note |
|---|---|---|
| company_name | opportunity.company | also feeds the opportunity name, see naming rule below |
| seat_count | opportunity.seat_count | numeric only; the form rejects a submission with no digit entered |
| contact_name | opportunity.primary_contact | |
| contact_email | opportunity.contact_email | |
| message | opportunity.notes (first entry) | Tom or Yuki expands on it after the first call |
| referrer_url | opportunity.source | tags whether the lead came from the showroom page, the pricing page, or a direct link |

Opportunity naming rule
Every opportunity created from the form takes the shape PW-<company-slug>-<seat count>seat, the company slug lowercased with spaces replaced by hyphens and punctuation dropped. A submission from Fernbrook Dental Group requesting 14 seats becomes PW-fernbrook-dental-14seat. Ray's form validation should catch a blank seat-count field before the post; on the rare submission that slips through, the opportunity is named without the seat suffix until Tom or Yuki confirms the number on the first call and renames it by hand.

Owner assignment rule
Since Yuki joined in August, new leads split by segment instead of defaulting to Tom. Architecture, legal, and coworking leads route to Tom; healthcare, financial services, and insurance leads route to Yuki; anything that fits neither bucket goes to whoever picked up the phone first, and that person leaves a short note so the other one doesn't also call the same prospect. An account Tom already has an open relationship with, whether an expansion or a renewal, stays with Tom no matter which segment it falls under now.

Failure modes
- Duplicate opportunity. A prospect who fills out the form twice, once from the pricing page and once from the showroom page, creates two opportunities carrying the same company slug. Two rows, one slug. Detect it on the Monday pipeline review; fix it by merging into the earlier-dated record and noting the second source in the opportunity's source history.
- Owner mis-assigned by segment. The form has no segment field of its own, so routing depends on Tom or Yuki reading the company name and the message and guessing right. Detect it when an opportunity sits untouched for more than two business days; fix it by reassigning in Pipewell with a short heads-up to whichever of them picks it up.
- Form post silently fails. Rare, but a Shopstack deploy has broken the form's Pipewell post twice this year. Detect it via a form-submission count that doesn't match Pipewell's new-opportunity count for the same day, which Tom checks weekly; the fix is Ray's, and it jumps to the front of his next contract day regardless of the Tuesday-Thursday cadence, given the lead-loss risk.

Change control
Ray owns the technical mapping, changed only Tuesdays and Thursdays and tested against a Pipewell sandbox opportunity first. Tom signs off on anything touching the naming rule or the owner-assignment logic, since a rename mid-pipeline confuses whoever is tracking the deal.

Verification
Tom reconciles the week's form submissions against new Pipewell opportunities every Monday morning, the same check that catches the duplicate and silent-failure cases above.
