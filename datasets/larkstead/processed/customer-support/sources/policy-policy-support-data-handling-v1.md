---
domain: customer-support
type: source
title: Policy Support Data Handling V1
tags:
- email-thread
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: email-thread
key_claims:
- id: policy-policy-support-data-handling-v1-01
  statement: Every support ticket in Helprise stores the customer's name, email address, order number, the full text of messages and agent replies, and attachments.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-02
  statement: Chat transcripts and their timestamps are stored the same way as email tickets.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-03
  statement: Helprise does not store payment details; those live in Shopstack.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-04
  statement: Ticket content, attachments, and chat transcripts are retained in Helprise for 24 months from the date the ticket is marked resolved.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-05
  statement: A ticket that gets reopened resets its own 24-month clock from the new resolution date.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-06
  statement: A customer may ask for their support data to be deleted before the 24-month period by emailing support@larkstead.example.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-07
  statement: An agent confirms the deletion request matches the account email, then submits the deletion inside Helprise.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-08
  statement: Helprise removes the ticket history within 5 business days after a deletion request.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-09
  statement: Tickets under an open defect investigation are retained past 24 months until that matter closes.
  confidence: high
  affects: []
- id: policy-policy-support-data-handling-v1-10
  statement: An agent can inform the customer of ticket retention without giving case detail.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/customer-support/policy/policy-support-data-handling-v1.md
provenance_hash: 8b77d8dab12feda3
---

# Policy Support Data Handling V1

## Summary

Policy: Support data handling and retention Doc: policy-support-data-handling-v1 Effective: 2024-09-03 Supersedes: none Owner: Priya Raman Approved by: Mara Voss, 2024-08-28 Applies to: all support interactions logged in Helprise ## 1. What Helprise stores Every support ticket in Helprise stores the customer's name, email address, order number where one is given, the full text of the customer's messages and the agent's replies, and any attachments the customer sends, most often photos of a damaged carton or a defective part.

## Content

Policy: Support data handling and retention
Doc: policy-support-data-handling-v1
Effective: 2024-09-03
Supersedes: none
Owner: Priya Raman
Approved by: Mara Voss, 2024-08-28
Applies to: all support interactions logged in Helprise

## 1. What Helprise stores

Every support ticket in Helprise stores the customer's name, email address, order number where one is given, the full text of the customer's messages and the agent's replies, and any attachments the customer sends, most often photos of a damaged carton or a defective part. Chat transcripts and their timestamps are stored the same way as email tickets. Helprise does not store payment details; those live in Shopstack, not in the support platform.

## 2. Retention period

Ticket content, attachments, and chat transcripts are retained in Helprise for 24 months from the date the ticket is marked resolved. After 24 months, the ticket and everything attached to it is purged automatically. There is no manual review step before deletion. A ticket that gets reopened resets its own 24-month clock from the new resolution date rather than the original one.

## 3. Requesting deletion

A customer may ask for their support data to be deleted before the 24-month period ends by emailing support@larkstead.example from the address on file with the request. An agent confirms the request matches the account email, then submits the deletion inside Helprise, which removes the ticket history within 5 business days. Order records in Shopstack and invoice records in Ledgerly sit outside the scope of this policy and follow their own retention rules.

## 4. Exceptions

Tickets under an open defect investigation, a carrier claim, or a legal hold are retained past 24 months until that matter closes, even against an incoming deletion request. An agent can tell the customer this is happening without giving case detail.

## Change note

First version of this policy. No prior version exists to supersede.
