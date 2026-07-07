# Template: troubleshooting guide (Helprise knowledge base)

Doc class: CUSTOMER-SUPPORT. Lives in the Helprise KB; agents link it in replies.
Read with `../company.yaml` and `../style-rules.md`.

## Skeleton

```
Doc: kb-<slug>
Title: <symptom in the customer's words>
Product: <product name> (<SKU>)
Applies to: <serial / lot / firmware / date ranges, exact>
Owner: <staff full name>
Created: <YYYY-MM-DD>
Last revised: <YYYY-MM-DD>

## Symptom

<1-3 sentences describing what the customer sees.>

## Before you start

<what to have on hand: order number, app version, serial location>

## Steps

1. <imperative step. One action per step.>
2. <step with expected result: "the status light turns solid white">
3. <branch: "If X, stop here. If Y, continue.">

## If the steps do not fix it

<escalation path: what the agent does next, who approves what, which
policy version applies.>

## Revision log

- <YYYY-MM-DD>: <one line, what changed and why>
- <YYYY-MM-DD>: <...>
```

## Exemplar (half length)

```
Doc: kb-vireo-sleep-flicker
Title: Vireo lamp flickers during sleep-mode dimming
Product: Vireo smart desk lamp (LS-LMP-001)
Applies to: units with Meridian PCB rev C (lots LOT-2025-31 and
LOT-2025-38) running firmware v1.2. Units on v1.0, v1.1, or v1.3 are not
affected.
Owner: Priya Raman
Created: 2025-12-22
Last revised: 2026-02-18

## Symptom

The lamp flickers at a slow, steady pulse, about one pulse every 2
seconds, whenever sleep-mode dimming takes brightness below 20 percent.
The flicker runs until brightness rises above 20 percent or dimming is
turned off.

## Before you start

Order number (#LS format) and the firmware version from the Vireo app,
Settings > About.

## Steps

1. Check the firmware version. If it shows v1.3 or later, this guide
   does not apply; escalate instead.
2. If it shows v1.2, run the update to v1.3 from the app. The update
   takes about 4 minutes and the lamp restarts twice.
3. If the update fails, unplug the power adapter for 30 seconds, plug it
   back in, and run the update again.
4. Confirm the fix: with the dim schedule on, set brightness to 15
   percent and wait one minute. No flicker means resolved.

## If the steps do not fix it

Replace the unit. Affected lamps carry the 2-year goodwill warranty
(effective 2026-01-20, scope vireo-v12-affected), so replacement is free
regardless of the standard 1-year term. Tag the ticket vireo-flicker.

## Revision log

- 2025-12-22: created with the interim workaround (disable sleep-mode
  dimming).
- 2026-02-18: v1.3 hotfix shipped 2026-02-17; workaround replaced with
  the update path.
```

## Realism notes

- The Applies-to block is exact: named lots, PCB revisions, firmware
  versions. "Some units" is a defect, and so is an open range like
  "LOT-2025-31 and later": LOT numbers are one global sequence across all
  vendors, so "and later" sweeps in Ostrava chair-base lots. Name the lots.
- This exemplar is SL4's canonical flicker guide rendered in Helprise KB
  form: same create date (2025-12-22), same symptom canon (steady ~0.5 Hz
  pulse below 20 percent brightness), same two lots, tag vireo-flicker. Do
  not mint a second guide for the same symptom with different facts.
- Guides accrete. The revision log preserves the workaround era; a guide
  created the day the symptom appeared and already containing the final fix
  is an anachronism.
- One action per step, with the expected result stated so the reader can
  tell success from failure.
- Customer-visible text never blames the vendor. Bitgrove Labs and Meridian
  appear in internal docs, not in the KB body.
- Escalation names the policy version and scope that make the replacement
  free. Agents copy this reasoning into tickets, so it has to be right.
- Dates must cohere with the storyline: v1.2 released 2025-12-09, hotfix
  v1.3 on 2026-02-17, goodwill extension effective 2026-01-20 (SL4).
