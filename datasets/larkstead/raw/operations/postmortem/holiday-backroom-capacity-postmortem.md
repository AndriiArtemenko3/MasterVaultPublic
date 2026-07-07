Postmortem: Portland backroom capacity, holiday shipping backlog
Published: 2025-01-10
Author: Dmitri Okafor
Incident window: 2024-12-06 to 2024-12-31
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes are stated as system and process failures.

Summary
Outbound order volume through the Portland backroom climbed sharply in the two weeks before Christmas, and the single packing bench and two-person crew that handle every other week of the year could not keep pace with it. Twenty-eight orders shipped more than two business days past their promise date between 2024-12-09 and 2024-12-20, well above a typical week's rate. Hank Morrow flagged the volume climb to Dmitri Okafor two days into the surge and requested a temporary packer, who started on the floor two days after that. The backlog cleared by the end of December, and every one of the 28 orders arrived complete. None were lost, none were damaged, they were simply late.

Impact
- 296 outbound orders processed through the backroom across the two-week window, against roughly 230 in a typical December week.
- 28 of the 296 shipped more than 2 business days late, a 9.5 percent rate, compared with 5 of 230 the week before, 2024-12-01 to 2024-12-06, a 2.2 percent rate.
- 9 tickets tracked customer-facing impact: HD-2024-64501 through HD-2024-64509.
- Zero units lost, damaged, or misdelivered among the 28 late shipments.

Timeline
| date | event |
|---|---|
| 2024-12-06 | prior week closes at 5 of 230 orders late, a normal rate |
| 2024-12-09 | peak window opens; order volume already running above the November baseline |
| 2024-12-10 | Hank Morrow flags the volume climb to Dmitri Okafor and requests a temp packer; approved the same day |
| 2024-12-12 | first backlog ticket, HD-2024-64501; temp packer starts on the backroom floor |
| 2024-12-20 | peak window closes at 28 of 296 orders shipped late |
| 2024-12-24 | last of the 9 tickets, HD-2024-64509, resolved |
| 2024-12-31 | year-end count confirms no lost or damaged units among the 28 |

Root cause
The backroom is provisioned for one packing bench and two staff, a footprint sized to the channel's non-peak baseline that has not scaled with year-over-year DTC order growth. No volume-triggered staffing plan exists for the weeks before Christmas, so the crew ran its standard headcount straight into the year's highest-volume week with nothing in reserve.

Contributing factors
- Temp labor gets requested and approved reactively rather than against a pre-cleared holiday plan, which cost two days between Hank's request and the temp packer's first day on the floor.
- The backroom gets one carrier pickup per day. Any day that closes over capacity rolls its overflow straight into the next day's count instead of clearing same-day.

What went well
- Hank caught the volume climb on the second day of the peak window rather than waiting for tickets to surface it, and the temp-labor request was approved the same day it went in.
- None of the 28 late orders involved a damaged or lost unit. Every one cleared, just later than promised, and the standard restocking and refund policies never entered the picture.

What went poorly
- The two-day gap between the request and the temp packer's start let the worst of the backlog build before extra hands reached the floor.
- This is not the first December the backroom has run into its ceiling, and no standing plan exists yet to see the surge coming before the order volume does.

Follow-ups
| action | owner | due | status |
|---|---|---|---|
| define a volume-triggered surge staffing plan for the weeks before Christmas | Hank Morrow | 2025-10-01 | not started |
| evaluate a third-party fulfillment option as a peak-week overflow valve | Dmitri Okafor | 2025-03-01 | in progress |
