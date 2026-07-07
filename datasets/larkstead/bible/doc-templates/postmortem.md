# Template: postmortem

Doc class: OPERATIONS. Blameless incident review with a timeline table.
Read with `../company.yaml` and `../style-rules.md`. Every date, count, and
dollar figure traces to the storyline spine; IDs are allocated in
`../storylines/*.yaml`.

## Skeleton

```
Postmortem: <incident name>
Published: <YYYY-MM-DD>
Author: <staff full name>
Incident window: <YYYY-MM-DD> to <YYYY-MM-DD>
Status: <closed | monitoring>

This is a blameless review. Names appear as actors in the timeline; causes
are stated as system and process failures.

Summary
<3-5 sentences: what happened, who was affected, how it ended>

Impact
- <numbers with bases: units, tickets, rates, dollars>

Timeline
| date | event |
|---|---|
| <YYYY-MM-DD> | <event, with doc or ticket reference> |

Root cause
<the mechanism, in system terms; no person named as a cause>

Contributing factors
- <the gap that let the root cause ship>

What went well
- <...>

What went poorly
- <...>

Follow-ups
| action | owner | due | status |
|---|---|---|---|
```

## Exemplar (half length)

```
Postmortem: Vireo firmware v1.2 sleep-dim flicker
Published: 2026-02-24
Author: Dmitri Okafor
Incident window: 2025-12-09 to 2026-02-17
Status: closed

This is a blameless review. Names appear as actors in the timeline; causes
are stated as system and process failures.

Summary
Firmware v1.2 shipped over the air on 2025-12-09 and put a visible 0.5 Hz
flicker on Vireo units built on Meridian PCB rev C whenever sleep-mode
dimming dropped below 20 percent brightness. 12 customers filed tickets. A
staged rollback to v1.1 contained the exposure in January and hotfix v1.3
closed it on 2026-02-17.

Impact
- 12 tickets against 618 exposed units, a 1.9 percent ticket rate.
- 592 units restored by the rollback; 26 recovered afterward through
  support contact or the v1.3 push.
- Cost: 4,800.00 SOW Amendment No. 2 (INV-BGL-2026-006) plus the 2-year
  goodwill warranty exposure on 618 units.

Timeline
| date | event |
|---|---|
| 2025-12-09 | firmware v1.2 released via the Bitgrove OTA service |
| 2025-12-11 | first flicker ticket, HD-2025-05402 |
| 2025-12-18 | pattern escalated: six identical tickets in seven days |
| 2025-12-19 | vendor bug report filed with Bitgrove Labs (VEN-03) |
| 2025-12-22 | customer troubleshooting guide published |
| 2026-01-09 | staged rollback to v1.1 begins; 592 of 618 restored by 13 Jan |
| 2026-01-20 | 2-year goodwill warranty, scope vireo-v12-affected |
| 2026-02-17 | hotfix v1.3 released; v1.1 channel pin removed |

Root cause
The v1.2 sleep-timer wake interrupt collides with the Meridian PCB rev C
LED driver PWM cycle below 20 percent duty. Rev B boards are unaffected.

Contributing factors
- The vendor test bench had no rev C hardware, so v1.2 shipped untested on
  rev C boards.

What went well
- The disable-sleep-dim workaround was confirmed by the fourth ticket
  (2025-12-15) and published 2025-12-22.
- The rollback restored 592 units in five days.

What went poorly
- The first six tickets were handled as one-off complaints for a week
  before the pattern was escalated.
- 26 offline units could not take the rollback and waited for individual
  contact or the v1.3 push.

Follow-ups
| action | owner | due | status |
|---|---|---|---|
| rev C units added to the Bitgrove test bench | VEN-03 | 2026-02-10 | done |
| Meridian rev D qualification | DO | 2026-03-31 | in progress |
| track warranty scope vireo-v12-affected in Helprise | PR | 2026-01-26 | done |
```

## Realism notes

- Blameless is structural, not a disclaimer: people appear only as actors
  ("Dmitri filed the bug report"), never as causes ("Dmitri failed to").
  Causes are mechanisms and missing safeguards.
- The timeline table is the spine. Every row must match the storyline spec
  (SL4 here) and the documents those rows reference must exist in the corpus.
- Impact numbers state their base: 12 of 618 is 1.9 percent; 592 + 26 = 618.
  Checkers recompute all of it.
- What-went-poorly items are process observations, not confessions, and each
  one should map to a follow-up row.
- Sections flex with the author and the incident. SL5's cutover postmortem
  ships with no What-went-well section at all; a missing section reads
  human, an empty or padded one reads generated.
- Published date sits after the incident window; a postmortem cannot cite
  events dated after its own publication (rev D is "in progress" here
  because qualification landed 2026-03-09, after this document).
- Corpus postmortems run 500-800 words per SL4; this exemplar is condensed.
