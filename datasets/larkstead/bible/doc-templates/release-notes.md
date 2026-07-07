# Template: release notes

Doc class: OPERATIONS. Version record for firmware (Vireo, via Bitgrove) or
storefront changes (Shopstack, via Ray Lindqvist). Read with
`../company.yaml` and `../style-rules.md`.

## Skeleton

```
Release notes: <product or system> <version>
Released: <YYYY-MM-DD>
Publisher: <vendor VEN-NN or staff full name>
Applies to: <SKUs / hardware revisions / lots>

Fixed
- <defect at mechanism level, with the affected configuration>

Changed
- <behavior changes>

Known issues
- <remaining limits, or "none open at release">

Rollout
<staging percentages and dates; the manual update path>

Support
<what support should do; ticket tags; warranty scope if any>
```

## Exemplar (half length)

```
Release notes: Vireo firmware v1.3
Released: 2026-02-17
Publisher: Bitgrove Labs (VEN-03) OTA service; posted by Dmitri Okafor
Applies to: all LS-LMP-001 units; the fix targets Meridian PCB rev C
(lots LOT-2025-31 and LOT-2025-38)

Fixed
- Sleep-timer wake interrupt rescheduled so it no longer collides with the
  rev C LED driver PWM cycle below 20 percent duty. This ends the 0.5 Hz
  sleep-dim flicker introduced in v1.2.

Changed
- Rev C units pinned to v1.1 since the January rollback return to the
  standard channel; the v1.1 pin is removed.

Known issues
- None open against v1.3 at release.

Rollout
Staged OTA: rev C units first, 25 percent on 2026-02-17, 100 percent by
2026-02-21. Manual path: Vireo app, Settings, Check for update.

Support
Sleep-mode dimming is safe to re-enable after the update. Notify customers
on the 12 vireo-flicker tickets. Affected units keep the 2-year goodwill
warranty, scope vireo-v12-affected, effective 2026-01-20.
```

## Realism notes

- Mechanism over marketing: say what collided and below what duty cycle,
  not "improved stability".
- Version history is immutable. The v1.2 notes (2025-12-09, SL4) claim a
  reworked dim schedule with no caveats; they were wrong in hindsight and
  they stay in the corpus unedited. Corrections ship as new versions.
- Staging is concrete: percentages with dates. The rollout order (rev C
  first) is itself information about the incident.
- Cross-references are exact: ticket tag vireo-flicker, warranty scope
  vireo-v12-affected, lots by LOT ID. Checkers grep them.
- Firmware notes come from the vendor and are posted by ops; Shopstack
  storefront notes come from Ray in commit-message style, referencing
  ticket IDs.
- Zero typos; ops register, short sections, no exclamation marks.
