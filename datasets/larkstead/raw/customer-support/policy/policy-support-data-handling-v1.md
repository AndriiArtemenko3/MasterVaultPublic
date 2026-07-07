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
