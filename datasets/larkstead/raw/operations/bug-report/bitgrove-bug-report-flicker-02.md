Bug report: vireo-v12-sleep-dim-flicker (follow-up)
Date: 2026-01-06
Filed by: Dmitri Okafor (DO)
Filed with: Bitgrove Labs (VEN-03)
System under report: Vireo firmware v1.2, released 2025-12-09
Related tickets: 9 to date, first HD-2025-05402 (2025-12-11)

Summary
Follow-up to our 2025-12-19 report, sent with debug log captures from three customer units. We've narrowed the root cause on our side and want to move on an interim rollback.

Reproduction steps
Unchanged from the first report: LS-LMP-001 with Meridian PCB rev C on v1.2, sleep-mode dim schedule enabled, brightness below 20 percent, flicker at roughly 0.5 Hz within 30 seconds.

Affected population
618 rev C units confirmed on firmware v1.2 as of today, out of 750 rev C units shipped across LOT-2025-31 and LOT-2025-38.

Evidence
Log captures pulled from three affected customer units all show the same signature: the firmware's sleep-timer wake interrupt fires and collides with the rev C LED driver's PWM cycle whenever duty is below 20 percent, which produces the visible 0.5 Hz flicker we've been reporting. This lines up with why rev B units, which use a different LED driver, don't reproduce the issue at all under the same conditions.

9 tickets to date, all on units purchased October 2025 or later, all on firmware v1.2. No new symptoms beyond the flicker itself, no reports of the lamp failing to power on or the app losing connection.

Ask
Given the mechanism above, we'd like a signed v1.1 image pinned to the OTA channel for rev C serials specifically, as an interim rollback while the permanent fix is developed. We understand this is a separate track from the hotfix; we just need the exposure contained now. Please confirm timing for the signed image so we can plan the rollback window with support and warehouse.
