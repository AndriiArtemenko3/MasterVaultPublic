Release notes: Vireo firmware v1.3
Released: 2026-02-17
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units; the fix targets Meridian PCB rev C, lots LOT-2025-31 and LOT-2025-38

Fixed
- Sleep-timer wake interrupt rescheduled so it no longer collides with the rev C LED driver PWM cycle at duty below 20 percent. This closes out the 0.5 Hz sleep-dim flicker introduced in v1.2 and reported across 12 support tickets between 2025-12-11 and 2026-01-14.

Changed
- Rev C units still pinned to the v1.1 rollback image return to the standard OTA channel; the pin put in place 09 Jan is removed as part of this release.

Known issues
- None open against v1.3 at release.

Rollout
Staged OTA, rev C units first: 25 percent on 2026-02-17, 100 percent by 2026-02-21. Manual path for anyone who wants it sooner: Vireo app, Settings, Check for update.

Support
Sleep-mode dimming is safe to re-enable once a unit is on v1.3. Notify customers on all 12 vireo-flicker tickets that the fix has shipped, including the ones already resolved by the January rollback or the workaround. Affected units keep the 2-year goodwill warranty, scope vireo-v12-affected, effective 2026-01-20, regardless of firmware version going forward.
