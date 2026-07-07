# Template: sales email thread

Doc class: SALES-CRM. External thread between a Larkstead seller and an
account contact, logged to Pipewell. Read with `../company.yaml` and
`../style-rules.md`.

## Skeleton

```
Thread: PW-<slug> correspondence, <YYYY-MM> [<n> messages]

=== Message 1 ===
From: <name@larkstead.example | contact@example.com>
To: <...>
Date: <YYYY-MM-DD HH:MM PT>
Subject: <subject>

<body per the sender's voice card>

=== Message 2 ===
From: <...>
To: <...>
Date: <later timestamp>
Subject: Re: <subject, unchanged once the chain starts>

<body>
<...messages in chronological order, top to bottom...>
```

## Exemplar (half length)

```
Thread: PW-bluebird-9seat correspondence, 2026-02 [3 messages]

=== Message 1 ===
From: yuki@larkstead.example
To: ruth.amadi@example.com
Date: 2026-02-09 14:05 PT
Subject: Larkstead quote: 9 workstations and treatment-room footrests

Dear Ruth,

Thank you for the call at 13:15 today. Confirming what we discussed:

9x Canopy team bundle (LS-BDL-002) at 1,149.00 each: 10,341.00
6x Finch footrest (LS-ACC-001) at 69.00 each: 414.00
Total: 10,755.00. Oregon delivery, no sales tax.

At 9 seats this order sits below our volume tier, so the prices above
are list. Next steps:

1. You confirm chair color by 2026-02-10.
2. I send the order form on 2026-02-11.
3. Delivery the week of 2026-02-23.

Regards,
Yuki Tanaka

=== Message 2 ===
From: ruth.amadi@example.com
To: yuki@larkstead.example
Date: 2026-02-10 09:32 PT
Subject: Re: Larkstead quote: 9 workstations and treatment-room footrests

Yuki - graphite black for all nine. One thing, the footrests go in
treatment rooms, can they be wiped down with clinic disinfectant?

Ruth

=== Message 3 ===
From: yuki@larkstead.example
To: ruth.amadi@example.com
Date: 2026-02-11 08:50 PT
Subject: Re: Larkstead quote: 9 workstations and treatment-room footrests

Dear Ruth,

Yes. The Finch surface is sealed polymer and tolerates standard clinic
disinfectants; avoid soaking the height hinge. The order form is
attached with graphite black for all 9 seats.

1. You return the signed form by 2026-02-12.
2. Delivery holds for the week of 2026-02-23.

Regards,
Yuki Tanaka
```

## Realism notes

- Two people, two registers, held for the whole thread. Yuki writes
  formally with no contractions, numbered next steps, absolute dates; Ruth
  writes two lines with a dash and no salutation. Averaging them is the
  single most common tell.
- Arithmetic in the body is exact and dated: 9 x 1,149.00 = 10,341.00 at
  the 2026-01-15 price, list because 9 seats is under the tier, Oregon so
  no tax. Bluebird closed-won 2026-02-12, so the thread must finish first.
- Subjects freeze after the first Re:. Nobody retitles a live thread.
- Timestamps are business-hours PT and gaps are human: overnight, then
  a morning reply. Yuki's confirm-in-writing-within-the-hour quirk pins
  Message 1 to just after the 13:15 call.
- Customers change the subject mid-thread (disinfectant) and the seller
  answers the tangent before returning to logistics. Threads that stay
  perfectly on-topic read scripted.
- The thread just ends. No "great doing business with you" coda unless a
  storyline needs one.
