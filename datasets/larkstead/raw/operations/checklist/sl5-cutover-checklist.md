Checklist: parcelpoint cutover, backroom shutdown
Version effective: 2026-01-08
Owner: Hank Morrow (HM)
Run for: full outbound cutover, portland backroom to parcelpoint
Run date: 2026-01-08   Completed by: HM

1. [x] final inventory transfer loaded on the thursday cascadia load, 2026-01-08. cascadia freight (VEN-06), full pallet count confirmed at dock.
2. [x] physical count taken before load-out. LS-ACC-001 on hand 62 units, counted twice.
3. [ ] portland backroom last pick 2026-01-09 at 17:00. any open order not picked by then transfers to the parcelpoint queue on 2026-01-12. not yet run, scheduled for tomorrow.
4. [ ] shopstack order feed flips to parcelpoint 2026-01-12 at 06:00 pt. ray confirms the webhook switch same morning.
5. [ ] bundle sku manual check each morning of cutover week, per the migration project risk list. not started, starts 01-12.
6. [ ] rollback plan on standby: if parcelpoint order acknowledgment rate drops below 95% at any point in cutover week, revert the shopstack webhook to the backroom queue within 4 hours. dmitri and hank both hold the revert switch.
7. [x] backroom staff notified shipping stops after the 01-09 last pick, no more parcel labels printed there after that.
8. [ ] carrier re-manifest check on any order labeled in the backroom before the 01-09 cutoff but not yet scanned by 01-13. not started, depends on item 3.

Notes
item 3 through 6 and item 8 depend on dates after this run, checked off as they close. rollback plan is a standby item, not something we expect to use, but it stays on the list until cutover week is done. dmitri has the same copy, we're both watching the acknowledgment rate starting 01-12.

Sign-off: HM, 2026-01-08
