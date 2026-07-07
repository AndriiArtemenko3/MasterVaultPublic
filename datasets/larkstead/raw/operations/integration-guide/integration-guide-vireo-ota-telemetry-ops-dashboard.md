Integration guide: Vireo OTA telemetry to the ops dashboard
Systems: Bitgrove Labs (VEN-03) telemetry API -> Ops dashboard (internal reporting tool)
Owner: Dmitri Okafor (DO)   Maintainer: Ray Lindqvist (RL)
Written: 2026-04-14

Schedule
Hourly pull, on the hour, Pacific time. The telemetry API sits behind the same Bitgrove OTA console operations already uses for staged rollouts, but this feed is read-only and separate from anything that pushes a build. Every Vireo unit checks in with Bitgrove when it wakes from standby, so the hourly pull is a snapshot of whatever's checked in since the last one, not a live stream.

Access
Ray holds the Bitgrove API credential for this feed, scoped to the read-only telemetry endpoint and nothing on the release or staging side of the console. Dmitri has dashboard view access only, no write access to the underlying data, since nothing about fleet counts should ever be hand-edited.

Endpoints
- /devices/telemetry: paginated device list, the main pull
- /devices/{serial}/status: single-unit lookup, used for one-off support escalations rather than the hourly job
- /devices/firmware-summary: a Bitgrove-side rollup Ray pulls once a day as a cross-check against what the dashboard computes independently

Field mapping
| Bitgrove field | Ops dashboard field | note |
|---|---|---|
| device.serial | unit_id | primary key on the dashboard side |
| device.firmware_version | firmware_version | v1.0 through v1.3 currently in the field |
| device.pcb_revision | hardware_rev | rev C and rev D are the two live revisions as of Meridian's 2026-03-09 qualification |
| device.last_checkin | last_seen_utc | drives the staleness check below |
| device.region | ship_zone | informational only, not used in any alert |

Firmware-version counts by hardware revision
The dashboard's main view is a small matrix, firmware version down one side and hardware revision across the top, with a live count in each cell. It exists so Dmitri can see the fleet's actual migration state at a glance rather than asking Bitgrove for a one-off export every time someone wants to know how many rev C units are still running an older build. An "unknown" row catches any serial whose firmware string doesn't parse against the four recognized versions, which happens more often than it should.

Alert thresholds
- Any single unit with a last_seen_utc older than 48 hours triggers a stale-device flag on the dashboard, surfaced to Dmitri but not paged.
- If the count of rev C units still running firmware older than v1.3 exceeds 25 in a single hourly pull, the dashboard fires an alert to Dmitri, since that combination is the one operations tracks most closely for OTA rollout progress.
- Alerts are suppressed for 72 hours after any staged release begins per the OTA rollout checklist, since a fresh push always produces a temporary spike in mixed-version counts that isn't itself a problem.

Failure modes
- Telemetry gap on an active unit: a device that was checking in normally goes silent for more than 48 hours without a corresponding delivery or return event. Detect via the stale-device flag; fix by opening a support ticket referencing the serial so an agent can confirm the unit's actual state with the customer rather than assuming a hardware fault.
- Malformed firmware string: a serial reports something outside v1.0 through v1.3, usually a leftover dev build tag from a bench unit that got shipped by mistake. Detect via a nonzero count in the unknown row; fix by having Ray patch the parsing rule once Bitgrove confirms what the string actually means.
- Bitgrove API timeout: the hourly pull can fail outright if the endpoint is slow to respond. Two consecutive missed pulls hold the dashboard on its last good snapshot rather than showing a blank matrix, and a third miss pages Ray directly instead of waiting for the next contract day.

Change control
Ray changes the field mapping and the alert thresholds only on Tuesdays and Thursdays, tested against a small set of bench-unit serials before touching the production feed. Dmitri approves any threshold change himself, since a threshold set too low turns into noise nobody acts on and one set too high hides a real migration problem.

Verification
Once a week, Dmitri checks the dashboard's firmware-by-hardware-revision matrix against the /devices/firmware-summary rollup Ray pulls the same day, confirming the two counts match cell for cell, and logs any discrepancy as a bug against the hourly job rather than editing the dashboard numbers to agree.
