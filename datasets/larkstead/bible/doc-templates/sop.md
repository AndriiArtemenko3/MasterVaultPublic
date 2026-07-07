# Template: SOP (standard operating procedure)

Doc class: OPERATIONS. One role, one task, numbered steps.
Read with `../company.yaml` and `../style-rules.md`. Use `process.md` instead
when the flow crosses teams or systems. Lot, ticket, and order IDs that belong
to a storyline are allocated in `../storylines/*.yaml`; never mint an ID a
storyline already owns.

## Skeleton

```
SOP: <title>
Effective: <YYYY-MM-DD> (supersedes <YYYY-MM-DD> version, when one exists)
Owner: <staff full name> (<initials>)
Applies to: <role or team>
Systems: <tools from company.yaml, e.g. Helprise, ParcelPoint portal>

Purpose
<One or two sentences: the task and the end state.>

Prerequisites
- <access, data, or condition required before step 1>

Steps
1. <verb-first action>
2. <action>. If <condition>, go to step <N>.
3. <...>

Escalation
<who, when, through what channel>

Close-out
<what gets recorded when a run of this SOP ends, if anything>

References
- <policy name + effective date / lot / ticket tag / vendor code>
```

## Exemplar (half length)

```
SOP: Vireo v1.1 rollback for rev C units
Effective: 2026-01-09
Owner: Dmitri Okafor (DO)
Applies to: support agents, warehouse
Systems: Helprise, Bitgrove OTA console, Mailloft

Purpose
Restore every Meridian PCB rev C Vireo unit from firmware v1.2 to the signed
v1.1 image. 618 units are in scope.

Prerequisites
- Bitgrove has pinned the OTA channel to the signed v1.1 image for rev C
  serials (lots LOT-2025-31 and LOT-2025-38)
- The unit must be powered and on Wi-Fi to take the rollback

Steps
1. Confirm the serial maps to LOT-2025-31 or LOT-2025-38. Any other lot:
   stop, this SOP does not apply.
2. Ask the customer to open the Vireo app and run Settings, Check for update.
3. The unit pulls the pinned v1.1 image. Confirm the About screen reads v1.1.
4. Tag the ticket vireo-flicker and note the restore date.
5. If the unit will not connect or will not update, schedule a second
   attempt inside the 09 Jan to 13 Jan window.
6. Still failing after 13 Jan: escalate.

Escalation
Unresolved units go to a replacement swap under warranty, referencing the
customer's HD ticket. Offline units get the outreach email via Mailloft.

Close-out
Result recorded 13 Jan: 592 of 618 units restored to v1.1; 26 units offline,
flagged for outreach.

References
- Bug reports to Bitgrove Labs (VEN-03), filed 2025-12-19 and 2026-01-06
- Warranty policy: 1 year standard, effective 2024-01-15
```

## Realism notes

- The owner's voice card governs the prose. Dmitri writes number-first,
  spells out units (618 units, 26 units), and dates things 09 Jan; a Priya
  SOP reads warmer and shorter.
- A revision is a new dated document with a supersedes line; versions never
  merge. Agents working an old ticket cite the SOP version in force on the
  ticket date.
- Steps start with a verb and branch as "If X, go to step N". A step the
  reader cannot execute ("handle appropriately") is a defect.
- Zero typos. SOPs share the policy register from style-rules.md.
- Cite canon by exact ID: lots, vendor codes, ticket tags, policy effective
  dates. Mechanical checkers grep them against company.yaml.
- Corpus SOPs run to the storyline's length band (vireo-v11-rollback-sop is
  350-600 words in SL4); the exemplar above is condensed.
