Release notes: Vireo firmware v1.2
Released: 2025-12-09
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units

Fixed
- Occasional ambient-sensor overshoot in bright rooms that caused the lamp to dim briefly when a door opened nearby. Recalibrated the sensor read cycle.

Changed
- Sleep-mode dimming schedule reworked: transitions between brightness steps are now smoother and the lamp holds each step slightly longer before easing to the next.
- Ambient sensor recalibration runs once per boot instead of once per week.

Known issues
- None open at release.

Rollout
Staged OTA to all connected LS-LMP-001 units: 20 percent on 09 Dec, 50 percent on 11 Dec, 100 percent on 13 Dec. Manual path for anyone who wants it sooner: Vireo app, Settings, Check for update.

Support
No hardware revisions are called out in this release package; the update applies the same way across every Vireo unit in the field. No customer-facing action needed. Tag any related tickets fw-v1.2 for tracking.
