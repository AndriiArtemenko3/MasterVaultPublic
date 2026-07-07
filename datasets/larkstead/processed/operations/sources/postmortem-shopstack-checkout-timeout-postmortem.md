---
domain: operations
type: source
title: Shopstack Checkout Timeout Postmortem
tags:
- postmortem
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: postmortem
key_claims:
- id: postmortem-shopstack-checkout-timeout-postmortem-01
  statement: For about 90 minutes on Saturday, 2025-03-08, Shopstack's checkout page waited longer than usual on payment authorization responses.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-02
  statement: Seventeen of 63 checkout attempts during the incident window were abandoned rather than resubmitted.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-03
  statement: Priya Raman's team caught the pattern from a run of tickets inside the first hour of the incident.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-04
  statement: Dmitri Okafor confirmed in the Shopstack admin panel that no customer had been charged without a completed order.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-05
  statement: Ray Lindqvist's support agreement covers only Tuesdays and Thursdays.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-06
  statement: The checkout module has never carried a 'processing, do not resubmit' state for any response delay.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-07
  statement: Uptime monitoring only alerts on outright processor failure, allowing slow-but-responding processors to produce no automated alert.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-08
  statement: The first signal of the incident came from four tickets inside one hour, after the window had already been running.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-09
  statement: Ana Petrova confirmed zero duplicate charges in the Ledgerly reconciliation pass on 2025-03-14.
  confidence: high
  affects: []
- id: postmortem-shopstack-checkout-timeout-postmortem-10
  statement: The retry-notice fix could not ship until Ray Lindqvist's next contract day, Tuesday 2025-03-11.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/postmortem/shopstack-checkout-timeout-postmortem.md
provenance_hash: 76c66222b0eeaaa8
---

# Shopstack Checkout Timeout Postmortem

## Summary

Postmortem: Shopstack checkout timeout, Saturday payment delay Published: 2025-03-13 Author: Dmitri Okafor Incident window: 2025-03-01 to 2025-03-14 Status: closed This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

## Content

Postmortem: Shopstack checkout timeout, Saturday payment delay
Published: 2025-03-13
Author: Dmitri Okafor
Incident window: 2025-03-01 to 2025-03-14
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

Summary
For about 90 minutes on Saturday, 2025-03-08, Shopstack's checkout page waited far longer than usual on payment authorization responses, and a generic error replaced the confirmation screen for any shopper whose response landed past the timeout. Seventeen of 63 checkout attempts in that window were abandoned rather than resubmitted. Priya Raman's team caught the pattern from a run of "did my order go through" tickets inside the first hour, and Dmitri Okafor confirmed through the Shopstack admin panel that no customer had been charged without a completed order. The retry-notice fix could not ship until Ray Lindqvist's next contract day, Tuesday 2025-03-11, three days after the incident, because his [[support]] agreement covers Tuesdays and Thursdays only.

Impact
- 63 checkout attempts during the 90-minute window; 17 abandoned after a payment-processor timeout, a 27 percent abandonment rate.
- Compares with 3 of 55 abandoned checkouts on the prior Saturday, 2025-03-01, a 5.5 percent rate.
- 6 tickets asking whether an order completed: HD-2025-64510 through HD-2025-64515.
- Zero duplicate charges, confirmed in Ana Petrova's 2025-03-14 reconciliation pass.

Timeline
| date | event |
|---|---|
| 2025-03-01 | prior Saturday closes at 3 of 55 abandoned checkouts, a normal rate |
| 2025-03-08 10:05 | payment-processor response times begin climbing past the usual 2-3 seconds |
| 2025-03-08 10:41 | first "did my order go through" ticket, HD-2025-64510 |
| 2025-03-08 10:58 | Priya Raman flags a pattern after a fourth ticket inside the hour |
| 2025-03-08 11:15 | Dmitri Okafor checks the Shopstack admin panel and confirms the latency spike |
| 2025-03-08 11:35 | processor response times return to normal on their own; window closes |
| 2025-03-11 | Ray Lindqvist ships the retry-notice fix on his next contract day |
| 2025-03-14 | Ana Petrova confirms zero duplicate charges in the Ledgerly reconciliation |

Root cause
Shopstack's checkout submits the payment authorization call and blocks the confirmation page behind that single response, with no client-side retry state. When the processor's response time rose from a typical 2-3 seconds to somewhere between 45 and 70 seconds during the window, the page returned a generic error with no indication of whether the charge had gone through, and roughly a quarter of the shoppers who hit it left instead of trying again.

Contributing factors
- Uptime monitoring only alerts on outright processor failure, a 5xx response or no response at all, so a slow-but-responding processor produced no automated alert of its own. The incident surfaced through ticket volume instead.
- The checkout module has never carried a "processing, do not resubmit" state for any response delay, regardless of cause, so a slow response has always looked identical to a hard outage from the customer's side.

What went well
- Priya's team caught the pattern within an hour and told affected customers to check their bank statement rather than resubmit, which likely held down further abandoned attempts once word spread through the queue.
- Dmitri's check of the admin panel ruled out a billing problem early, so the fix could focus on messaging rather than a reconciliation exercise.

What went poorly
- No real-time alert caught the degraded response time. The first signal was four tickets inside one hour, after the window had already been running for over half its length.
- The underlying gap, no retry messaging on a slow response, stayed live through the weekend since Ray's contract covers Tuesday and Thursday only. The processor recovered on its own within the 90 minutes and no second slowdown occurred before Tuesday's fix shipped, but the exposure sat there the whole time regardless.

Follow-ups
| action | owner | due | status |
|---|---|---|---|
| ship a retry, do-not-resubmit notice on checkout timeout | Ray Lindqvist | 2025-03-11 | done |
| add a processor-latency alert separate from the uptime check | Ray Lindqvist | 2025-04-10 | in progress |
| confirm zero duplicate charges via Ledgerly reconciliation | Ana Petrova | 2025-03-14 | done |
