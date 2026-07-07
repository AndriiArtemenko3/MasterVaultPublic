SOP: Vireo v1.1 rollback for rev C units
Effective: 2026-01-09
Owner: Dmitri Okafor (DO)
Applies to: support agents, warehouse
Systems: Helprise, Bitgrove OTA console, Mailloft

Purpose
Restore every Meridian PCB rev C Vireo unit from firmware v1.2 back to the signed v1.1 image while Bitgrove develops a permanent fix. 618 units are confirmed on v1.2 and in scope for this rollback.

Prerequisites
- Bitgrove Labs (VEN-03) has pinned the OTA channel to a signed v1.1 image for rev C serials, covering lots LOT-2025-31 (400 units) and LOT-2025-38 (350 units)
- The unit must be powered on and connected to Wi-Fi to pull the pinned image; units that are unplugged or offline cannot receive it automatically
- Support has the current list of the 618 v1.2-confirmed serials pulled from Helprise before starting outreach

Steps
1. Confirm the customer's unit serial maps to LOT-2025-31 or LOT-2025-38. Any other lot, stop here, this SOP does not apply and the unit stays on v1.1 or newer as normal.
2. For units already connected and online, no customer action is needed; the pinned image reaches them automatically as part of the staged rollout below.
3. For units flagged as inactive in Helprise, contact the customer and ask them to open the Vireo app, then Settings, Check for update. If the unit is offline, ask them to confirm it's plugged in and on their home Wi-Fi first.
4. Once the customer checks for the update, confirm with them that the app's About screen now reads v1.1. If it does, tag the ticket vireo-flicker and note the restore date in the ticket.
5. If the unit will not connect or will not pull the update on the first attempt, schedule a second attempt with the customer inside the 09 Jan to 13 Jan rollback window.
6. If the unit is still on v1.2 after 13 Jan, escalate per below rather than continuing to retry.

Escalation
Units that cannot be restored by 13 Jan go to a replacement swap under warranty, referencing the customer's HD ticket number so the swap is tied to the original order. Units that remain offline and unreachable after two outreach attempts get flagged for a follow-up outreach email sent through Mailloft, separate from the individual ticket replies already in progress.

Close-out
Result recorded at close of the rollback window: 592 of 618 units restored to v1.1 by 13 Jan 2026. 26 units remained offline and unreachable, flagged for the Mailloft outreach email and individual follow-up as capacity allows.

References
- Bug reports to Bitgrove Labs (VEN-03), filed 2025-12-19 and 2026-01-06
- Warranty policy in force at rollback: 1 year standard, effective 2024-01-15
- Ticket tag: vireo-flicker
