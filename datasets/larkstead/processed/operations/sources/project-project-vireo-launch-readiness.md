---
domain: operations
type: source
title: Project Vireo Launch Readiness
tags:
- project
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: project
key_claims:
- id: project-project-vireo-launch-readiness-01
  statement: Dmitri Okafor owns the Vireo smart desk lamp project.
  confidence: high
  affects: []
provenance: datasets/larkstead/raw/operations/project/project-vireo-launch-readiness.md
provenance_hash: 7909edd49ab2a399
---

# Project Vireo Launch Readiness

## Summary

Project: Vireo smart desk lamp launch readiness Owner: Dmitri Okafor (DO) Opened: 2024-08-01 Target close: 2024-10-15 Related: VEN-07, VEN-03, VEN-09, LS-LMP-001 Goal Launch the Vireo smart desk lamp (LS-LMP-001) on Shopstack by 2024-10-15 at 149.00, with 800 units landed at the Portland backroom, firmware v1.0 preloaded, and the product liability rider bound before the first unit ships. Plan - Meridian Components (VEN-07) runs the first production batch of the Vireo PCB, 800 units, 6-week lead time.

## Content

Project: Vireo smart desk lamp launch readiness
Owner: Dmitri Okafor (DO)
Opened: 2024-08-01   Target close: 2024-10-15
Related: VEN-07, VEN-03, VEN-09, LS-LMP-001

Goal
Launch the Vireo smart desk lamp (LS-LMP-001) on Shopstack by 2024-10-15 at
149.00, with 800 units landed at the Portland backroom, firmware v1.0
preloaded, and the product liability rider bound before the first unit
ships.

Plan
- Meridian Components (VEN-07) runs the first production batch of the Vireo
  PCB, 800 units, 6-week lead time. This is the same vendor already on
  contract for Sparrow cable kits and power adapters, so the account setup
  is done, only the new part number needs qualifying.
- Bitgrove Labs (VEN-03) delivers firmware v1.0 for acceptance testing: two
  test cycles across 40 units before the build is signed off, covering
  sleep timing, dimming presets, and the OTA update path end to end.
- Nehalem Mutual Insurance (VEN-09) product liability rider has to bind
  before launch day. Larkstead hasn't sold anything with a heating or
  battery element before, and legal flagged this months back as a hard
  gate, not a nice-to-have.
- Power adapter ships in the box with the lamp. There's no standalone
  adapter SKU yet, that's a later addition if the return rate on damaged
  adapters ever justifies one.
- Packaging: GreenCrate (VEN-05) mailer, light-weight item, same lane as
  the Wren stand and the Alder mats.
- Landing at the Portland backroom, same as every other light SKU this
  year. Pricing at 149.00 list, 52.00 landed cost per unit.
- Shopstack listing includes a firmware version line and an OTA notice so
  the first update, whenever Bitgrove ships one, doesn't surprise anyone
  reading the product page cold.

Risks
- Meridian's PCB run slips past late September: fallback trims day-one
  stock rather than moving the 15 Oct date, since the insurance rider is
  written to bind on a specific date regardless of stock position.
- Bitgrove firmware acceptance finds a blocking bug during the two test
  cycles: fallback is a hard firmware freeze on 01 Oct, and if v1.0 isn't
  clean by then the launch slips a full week rather than shipping a build
  that hasn't passed both cycles.
- Nehalem rider isn't bound by 01 Oct: fallback is holding the launch date
  entirely, no exceptions, since selling an uninsured electronic item isn't
  a call Dmitri gets to make alone.

Log
01 Aug: kickoff. Meridian quote requested for the 800-unit PCB run.
14 Aug: Meridian confirms 800 units, 6-week lead, ships 25 Sep.
02 Sep: Bitgrove delivers the firmware v1.0 candidate build for acceptance
testing.
16 Sep: firmware v1.0 accepted after 2 test cycles across 40 units, zero
failures logged on either cycle.
25 Sep: 800 units ship ex-Meridian on schedule.
30 Sep: Nehalem Mutual product liability rider signed, binds 01 Oct.
09 Oct: 800 units land at the Portland backroom. Hank's team spot-checks 24
units, clean, no shipping damage.
15 Oct: Vireo live on Shopstack at 149.00, firmware v1.0 preloaded, OTA
notice on the listing.
