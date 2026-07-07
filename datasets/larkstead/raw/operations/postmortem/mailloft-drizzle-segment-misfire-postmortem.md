Postmortem: Mailloft Drizzle campaign, B2B segment misfire
Published: 2024-08-16
Author: Dmitri Okafor
Incident window: 2024-07-29 to 2024-08-15
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

Summary
The Drizzle promotional email went out in two scheduled waves on the morning of 2024-08-05, and wave one reached 30 B2B contacts who should have been excluded from consumer marketing sends entirely. Mailloft's exclusion filter keys off a channel tag applied at contact import, and a batch of 46 Pipewell-sourced contacts imported on 2024-07-29 carried no tag at all, so the filter had nothing to key off. Sofia Grieg pulled the remaining 16 untagged contacts before wave two fired 90 minutes later, which kept the exposure to that one batch. Four of the 30 exposed contacts unsubscribed from all Larkstead marketing within three days, one of them the primary contact on an open B2B opportunity at the time. Ray Lindqvist shipped a filter fix on his next contract day, and no further misdirected sends have occurred since.

Impact
- 46 of roughly 9,200 Drizzle recipients were B2B contacts that should have carried a channel-exclusion tag and did not, a tagging gap of about 0.5 percent of the send.
- 30 of the 46 went out in wave one before the miss was caught; the other 16 were pulled from wave two before it sent.
- 4 of the 30 wave-one recipients unsubscribed from all marketing between 2024-08-05 and 2024-08-08, including Elena Brandt at Harborview Architects, then an open B2B account.
- One support ticket, HD-2024-64500, came in from a fifth misdirected contact who asked to be dropped from consumer email specifically rather than unsubscribing outright.

Timeline
| date | event |
|---|---|
| 2024-07-29 | Pipewell weekly contact export imported to Mailloft; 46 records carry no channel tag |
| 2024-08-05 08:00 | Drizzle wave one sent to 5,000 contacts, 30 of them untagged B2B contacts |
| 2024-08-05 08:52 | Elena Brandt emails Tom Aldridge asking why she got a consumer discount mailer |
| 2024-08-05 09:05 | Tom flags Sofia Grieg; Sofia confirms the segment miss inside minutes |
| 2024-08-05 09:20 | Sofia pulls the remaining 16 untagged contacts ahead of wave two's 09:30 send |
| 2024-08-06 | HD-2024-64500 filed |
| 2024-08-08 | fourth unsubscribe recorded; none logged after this date |
| 2024-08-13 | Ray Lindqvist begins the segment-filter fix on his Tuesday contract day |
| 2024-08-15 | fix deployed; untagged contacts now default to excluded rather than included |

Root cause
Mailloft's send-segment builder treats an untagged contact as included in the broad marketing list by default, and excludes on tag presence rather than including on it. Any contact reaching Mailloft with no channel tag falls into every consumer send until someone notices, and this time nobody did until a recipient asked why she'd received it.

Contributing factors
- The 2024-07-29 Pipewell export predates the point where channel tagging became a required field on that weekly sync, so the 46 records carried no tag through no fault of the export process itself.
- No pre-send audit compares an outgoing segment's count against Pipewell's known B2B contact count, so a gap of this size had nothing automated standing in its way before the send fired.

What went well
- Sofia caught the miss within an hour of wave one going out, off a direct email from an affected contact rather than a dashboard alert, and stopped wave two from repeating the exposure to the same 16 people.
- Only one of the five affected contacts who reached out over it escalated to a support ticket. The rest were handled by Sofia directly with the unsubscribe confirmation and a short note.

What went poorly
- Eight days passed between wave one and Ray's next contract day. Any second campaign scheduled in that window would have carried the same risk with no fix in place yet.
- Nobody checked whether the 2024-07-29 import carried tags before scheduling Drizzle six days later. The two events weren't connected until the misfire made the connection obvious.

Follow-ups
| action | owner | due | status |
|---|---|---|---|
| flip the segment builder default to exclude untagged contacts | Ray Lindqvist | 2024-08-15 | done |
| add a pre-send segment-count check against Pipewell's B2B contact list | Sofia Grieg | 2024-09-15 | not started |
| backfill channel tags on every pre-2024-07-29 Pipewell import | Tom Aldridge | 2024-08-30 | in progress |
