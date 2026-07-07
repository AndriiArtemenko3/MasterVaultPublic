---
domain: customer-support
type: source
title: Kb Vireo Wifi Dropout
tags:
- other
status: processed
created: '2026-07-07'
updated: '2026-07-07'
source_type: other
key_claims:
- id: troubleshooting-kb-vireo-wifi-dropout-01
  statement: The Vireo smart desk lamp (LS-LMP-001) drops offline or never appears in the app's device list during setup.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-02
  statement: The lamp connects only on 2.4 GHz Wi-Fi.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-03
  statement: A single combined Wi-Fi name can drop the lamp or block it from joining.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-04
  statement: The lamp should sit within about 30 feet of the router with no more than one wall between them.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-05
  statement: Re-pair the lamp by holding the base button for 8 seconds until the status light blinks blue.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-06
  statement: A spare power adapter (LS-ACC-012, 22.00) should be tried if the lamp drops within the hour.
  confidence: high
  affects: []
- id: troubleshooting-kb-vireo-wifi-dropout-07
  statement: A persistent join failure indicates a hardware fault in the lamp's Wi-Fi module.
  confidence: medium
  affects: []
provenance: datasets/larkstead/raw/customer-support/troubleshooting/kb-vireo-wifi-dropout.md
provenance_hash: 199119a9a862167e
---

# Kb Vireo Wifi Dropout

## Summary

Doc: kb-vireo-wifi-dropout Title: Vireo lamp keeps disconnecting from Wi-Fi, or won't show up in the app at all Product: Vireo smart desk lamp (LS-LMP-001) Applies to: units on firmware v1.0 or v1.1, current as of May 2025. Router and network settings only; not a hardware or lot issue.

## Content

Doc: kb-vireo-wifi-dropout
Title: Vireo lamp keeps disconnecting from Wi-Fi, or won't show up in the app at all
Product: Vireo smart desk lamp (LS-LMP-001)
Applies to: units on firmware v1.0 or v1.1, current as of May 2025. Router and network settings only; not a hardware or lot issue.
Owner: Celeste Marin
Created: 2025-05-12
Last revised: 2025-05-12

## Symptom

The app shows the lamp as connected for a while and then it drops offline, or the lamp never appears in the app's device list during setup even though the status light on the base is lit.

## Before you start

Ask for the order number, and whether the home router broadcasts one combined Wi-Fi name or two separate names for the 2.4 GHz and 5 GHz bands.

## Steps

1. Check the router's band setup first. Vireo only connects on 2.4 GHz; a single "smart" network name that automatically pushes newer devices onto 5 GHz will drop the lamp or block it from joining in the first place.
2. If the router has two separate names, connect to the one labeled 2.4 GHz specifically, not whichever name the customer's phone happens to be on at the time.
3. If there's only one combined name, go into the router's settings and either add a dedicated 2.4 GHz-only network or turn off "band steering" so the lamp can hold that connection without being bumped over.
4. Check distance next. The lamp should sit within about 30 feet of the router with no more than one wall between them; move it closer just for the pairing attempt if it's currently farther.
5. Re-pair the lamp: hold the base button for 8 seconds until the status light blinks blue, then add the device again from the app's home screen.
6. Confirm the fix by leaving the lamp connected, untouched, for a full hour. Staying online that whole stretch is the real test, not just reconnecting once and calling it done.

## If the steps do not fix it

If the network is confirmed 2.4 GHz and the distance checks out but the lamp still drops within the hour, try a spare power adapter (LS-ACC-012, 22.00) before anything else; an unstable adapter can look exactly like a Wi-Fi problem. If swapping the adapter doesn't help either, escalate to Priya for a unit-level check. Both v1.0 and v1.1 are stable, widely shipped releases, so a persistent join failure at that point points to a hardware fault in the lamp's Wi-Fi module rather than a settings issue, and it should be handled as a standard warranty case.

## Revision log

- 2025-05-12: created.
